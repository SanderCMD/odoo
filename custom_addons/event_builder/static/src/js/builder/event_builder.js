import { Component, onWillStart, useState } from '@odoo/owl';
import { rpc } from '@web/core/network/rpc';
import { formatCurrency } from '@web/core/currency';
import { debounce } from '@web/core/utils/timing';

/**
 * De configurator zelf.
 *
 * Dit is een OWL-component: Odoo's eigen framework voor herbruikbare
 * stukjes interface (vergelijkbaar met React/Vue). Drie dingen om te weten:
 *
 * 1. `static template` verwijst naar de XML-template in event_builder.xml.
 *    De HTML staat daar, de logica hier.
 * 2. `useState()` maakt een object "reactief": wijzig je er iets in, dan
 *    hertekent OWL automatisch de stukjes HTML die die waarde gebruiken.
 *    Je hoeft dus nooit zelf de DOM aan te passen.
 * 3. `this` verwijst naar de component-instantie, net zoals `self` in Python
 *    naar het object verwijst. In JavaScript is het impliciet beschikbaar.
 */
export class EventBuilder extends Component {
    static template = 'event_builder.EventBuilder';
    static props = {
        // 0 betekent: laat de server de eerste actieve configurator kiezen.
        configId: { type: Number, optional: true },
    };
    static defaultProps = { configId: 0 };

    setup() {
        this.state = useState({
            loading: true,
            error: null,
            config: null,

            // Wat de bezoeker invulde
            guestCount: 0,
            dateFrom: '',
            dateTo: '',
            zip: '',
            selectedOptionIds: [],

            // Wat de server terugrekende
            quote: null,
            pricing: false,
            submitting: false,
        });

        // De prijsberekening gebeurt op de server. Bij elke klik meteen een
        // aanvraag versturen zou tientallen calls per seconde geven, dus
        // `debounce` wacht tot de bezoeker 250ms stil is. Dat is snel genoeg
        // om "live" te voelen en traag genoeg om de server te sparen.
        this.debouncedRefreshPrice = debounce(this.refreshPrice.bind(this), 250);

        onWillStart(async () => {
            try {
                const config = await rpc('/event_builder/config', {
                    config_id: this.props.configId || null,
                });
                this.state.config = config;
                this.state.guestCount = config.guest.default;
                this.setDefaultDates(config.min_lead_days);
            } catch {
                this.state.error = 'De configurator kon niet geladen worden.';
            } finally {
                this.state.loading = false;
            }
        });
    }

    /** Stel standaarddatums voor: net na de minimale doorlooptijd, 1 dag lang. */
    setDefaultDates(minLeadDays) {
        const from = new Date();
        from.setDate(from.getDate() + (minLeadDays || 0) + 1);
        const to = new Date(from);
        to.setDate(to.getDate() + 1);
        this.state.dateFrom = from.toISOString().slice(0, 10);
        this.state.dateTo = to.toISOString().slice(0, 10);
    }

    /** Vandaag + doorlooptijd, als minimum voor de datumvelden in de browser. */
    get minDate() {
        const d = new Date();
        d.setDate(d.getDate() + (this.state.config?.min_lead_days || 0));
        return d.toISOString().slice(0, 10);
    }

    get durationDays() {
        return this.state.quote?.days || 1;
    }

    /** Verplichte stappen waar nog niets gekozen is. */
    get missingSteps() {
        if (!this.state.config) {
            return [];
        }
        return this.state.config.steps.filter(
            (step) => step.is_required && !step.options.some(
                (option) => this.isSelected(option.id)
            )
        );
    }

    get canSubmit() {
        return (
            !this.state.submitting
            && this.state.selectedOptionIds.length > 0
            && this.missingSteps.length === 0
            && !!this.state.dateFrom
            && !!this.state.dateTo
        );
    }

    isSelected(optionId) {
        return this.state.selectedOptionIds.includes(optionId);
    }

    /**
     * Klik op een optie. Bij een 'single'-stap vervangt de keuze de vorige
     * binnen diezelfde stap; bij 'multi' wordt ze toegevoegd of weggehaald.
     */
    toggleOption(step, optionId) {
        const selected = new Set(this.state.selectedOptionIds);

        if (step.selection_type === 'single') {
            const wasSelected = selected.has(optionId);
            for (const option of step.options) {
                selected.delete(option.id);
            }
            if (!wasSelected) {
                selected.add(optionId);
            }
        } else if (selected.has(optionId)) {
            selected.delete(optionId);
        } else {
            selected.add(optionId);
        }

        this.state.selectedOptionIds = [...selected];
        this.debouncedRefreshPrice();
    }

    onGuestCountChange(ev) {
        const config = this.state.config;
        let value = parseInt(ev.target.value, 10) || 0;
        value = Math.min(Math.max(value, config.guest.min), config.guest.max);
        this.state.guestCount = value;
        ev.target.value = value;
        this.debouncedRefreshPrice();
    }

    onDateChange() {
        // Ophaaldatum nooit voor de leverdatum laten staan.
        if (this.state.dateTo && this.state.dateTo < this.state.dateFrom) {
            this.state.dateTo = this.state.dateFrom;
        }
        this.debouncedRefreshPrice();
    }

    /**
     * Haal de actuele prijs op bij de server.
     *
     * Bewust GEEN prijsberekening hier in JavaScript: btw, prijslijsten en
     * afrondingen moeten exact overeenkomen met wat straks op de offerte
     * staat. Eén bron van waarheid, en die staat op de server.
     */
    async refreshPrice() {
        if (!this.state.selectedOptionIds.length) {
            this.state.quote = null;
            return;
        }
        this.state.pricing = true;
        try {
            this.state.quote = await rpc('/event_builder/price', {
                config_id: this.props.configId || null,
                guest_count: this.state.guestCount,
                date_from: this.state.dateFrom || null,
                date_to: this.state.dateTo || null,
                option_ids: this.state.selectedOptionIds,
            });
            this.state.error = null;
        } catch (error) {
            this.state.error = error?.data?.message || 'De prijs kon niet berekend worden.';
        } finally {
            this.state.pricing = false;
        }
    }

    async onSubmit() {
        if (!this.canSubmit) {
            return;
        }
        this.state.submitting = true;
        try {
            const result = await rpc('/event_builder/submit', {
                config_id: this.props.configId || null,
                guest_count: this.state.guestCount,
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                zip_code: this.state.zip,
                option_ids: this.state.selectedOptionIds,
            });
            window.location = result.redirect_url;
        } catch (error) {
            this.state.error = error?.data?.message || 'Er ging iets mis. Probeer opnieuw.';
            this.state.submitting = false;
        }
    }

    formatPrice(amount) {
        if (!this.state.quote) {
            return '';
        }
        return formatCurrency(amount, this.state.quote.currency_id);
    }
}
