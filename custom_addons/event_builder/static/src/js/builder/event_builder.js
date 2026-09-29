import { Component, onWillStart, useRef, useState } from '@odoo/owl';
import { rpc } from '@web/core/network/rpc';
import { formatCurrency } from '@web/core/currency';
import { debounce } from '@web/core/utils/timing';
import { _t } from '@web/core/l10n/translation';

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
        // Eén verwijzing naar de buitenste div. Van daaruit zoeken we de
        // stappen en strips op via data-attributen. Dat is eenvoudiger dan een
        // aparte ref per stap, want het aantal stappen komt pas uit de
        // database en ligt dus niet op voorhand vast.
        this.rootRef = useRef('root');

        this.state = useState({
            loading: true,
            error: null,
            config: null,

            // Wat de bezoeker invulde
            guestCount: 0,
            dateFrom: '',
            dateTo: '',
            selectedOptionIds: [],

            // Adressen. Alleen van toepassing voor ingelogde klanten; de
            // server vult ze zelf met het standaard lever- en billing address.
            isLoggedIn: false,
            addresses: null,          // payload van /event_builder/addresses
            useDeliveryAsBilling: true,
            // Welke van de twee blokken openstaat: null, 'delivery' of
            // 'invoice'. Samengevouwen tonen ze het adres op één regel.
            expandedAddress: null,
            addressDraft: null,       // de velden die nu bewerkt worden
            addressPristine: null,    // ijkpunt om wijzigingen te herkennen
            canRenameAddress: true,   // false op een hoofdcontact
            canEditVat: true,         // false zodra er facturen uitgingen
            vatLabel: 'VAT number',   // heet anders per land
            addressSaving: false,
            addressError: null,
            // De landenlijst wordt pas opgehaald zodra iemand een adres
            // openklapt, en daarna hergebruikt.
            countryList: null,
            countriesLoading: false,

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
                this.state.isLoggedIn = !!config.is_logged_in;
                this.setDefaultDates(config.min_lead_days);

                // Een configuratie die vóór het inloggen gemaakt werd,
                // terugzetten. Zonder dit komt de klant na de login op een
                // lege configurator terecht en haakt hij af.
                this.restoreDraft();

                if (this.state.isLoggedIn) {
                    await this.loadAddresses();
                }
                if (this.state.selectedOptionIds.length) {
                    await this.refreshPrice();
                }
            } catch {
                this.state.error = _t('The configurator could not be loaded.');
            } finally {
                this.state.loading = false;
            }
        });
    }

    // ------------------------------------------------------------------
    // Bewaren over de login heen
    // ------------------------------------------------------------------

    get draftKey() {
        return `event_builder_draft_${this.props.configId || 0}`;
    }

    /**
     * De keuze even opzijzetten voor we naar de loginpagina sturen.
     *
     * sessionStorage en niet localStorage: de keuze moet de omweg langs de
     * loginpagina overleven, maar hoeft er volgende week niet nog te staan.
     * Het gaat om een handvol ids en datums, en er staat niets gevoeligs in.
     * De browser kan opslag weigeren (privémodus), dus alles in een try/catch.
     */
    saveDraft() {
        try {
            sessionStorage.setItem(this.draftKey, JSON.stringify({
                guestCount: this.state.guestCount,
                dateFrom: this.state.dateFrom,
                dateTo: this.state.dateTo,
                selectedOptionIds: this.state.selectedOptionIds,
            }));
        } catch {
            // Geen opslag beschikbaar: dan gaat de keuze verloren na de login.
            // Vervelend, maar geen reden om het inloggen te blokkeren.
        }
    }

    restoreDraft() {
        let draft = null;
        try {
            draft = JSON.parse(sessionStorage.getItem(this.draftKey) || 'null');
            sessionStorage.removeItem(this.draftKey);
        } catch {
            return;
        }
        if (!draft) {
            return;
        }
        Object.assign(this.state, {
            guestCount: draft.guestCount ?? this.state.guestCount,
            dateFrom: draft.dateFrom || this.state.dateFrom,
            dateTo: draft.dateTo || this.state.dateTo,
            selectedOptionIds: draft.selectedOptionIds || [],
        });
    }

    /** Naar de loginpagina, met een terugkeeradres naar deze pagina. */
    goToLogin() {
        this.saveDraft();
        const redirect = encodeURIComponent(
            window.location.pathname + window.location.search
        );
        window.location = `/web/login?redirect=${redirect}`;
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

    // ------------------------------------------------------------------
    // Adressen
    // ------------------------------------------------------------------

    async loadAddresses() {
        this.state.addresses = await rpc('/event_builder/addresses', {});
        this.state.useDeliveryAsBilling = this.state.addresses.use_delivery_as_billing;
    }

    /** Het adres dat nu voor dit type gekozen is. */
    address(type) {
        return this.state.addresses?.[type] || null;
    }

    /** Het billing address volgt het delivery address zodra het vinkje aanstaat. */
    get effectiveInvoice() {
        return this.state.useDeliveryAsBilling
            ? this.address('delivery')
            : this.address('invoice');
    }

    /** Welke adressen nog niet volledig zijn. Stuurt knop en waarschuwing. */
    get incompleteAddresses() {
        if (!this.state.isLoggedIn || !this.state.addresses) {
            return [];
        }
        // Sleutels, geen woorden. Vergelijken op een Nederlands woord zou
        // breken zodra de site vertaald wordt.
        const result = [];
        if (!this.address('delivery')?.is_complete) {
            result.push('delivery');
        }
        if (!this.state.useDeliveryAsBilling && !this.address('invoice')?.is_complete) {
            result.push('invoice');
        }
        return result;
    }

    /** De leesbare naam van een adrestype, in de taal van de bezoeker. */
    addressTypeLabel(type) {
        return type === 'invoice' ? _t('billing address') : _t('delivery address');
    }

    get incompleteAddressLabels() {
        return this.incompleteAddresses.map((type) => this.addressTypeLabel(type));
    }

    /**
     * Een adresblok open- of dichtklappen.
     *
     * Samengevouwen is het één regel tekst; uitgeklapt wordt het een
     * formulier. We maken bij het openen een kopie van de waarden, zodat
     * annuleren niets wijzigt en de weergave niet meebeweegt terwijl je typt.
     */
    toggleAddress(type) {
        if (this.state.expandedAddress === type) {
            // Dichtklappen is hetzelfde als annuleren: de wijzigingen gaan
            // weg. Zo kan er nooit iets onbewaard blijven rondslingeren
            // terwijl de boekknop weer vrijgeeft.
            this.cancelAddressEdit();
            return;
        }
        this.state.expandedAddress = type;
        this.state.addressError = null;
        this.fillDraft(this.address(type));
        // Niet awaiten: het formulier mag meteen verschijnen, de landen
        // druppelen daarna binnen.
        this.ensureCountries();
    }

    get countries() {
        return this.state.countryList || [];
    }

    /**
     * De verplichte velden voor het adres dat nu openstaat.
     *
     * De lijst komt van de server, want ze hangt af van het land: Odoo vraagt
     * in de Verenigde Staten een staat en in Belgie niet. Zelf raden zou
     * betekenen dat de sterretjes in het formulier iets anders zeggen dan de
     * controle bij het opslaan.
     */
    get requiredFields() {
        return this.address(this.state.expandedAddress)?.mandatory || [];
    }

    isRequiredField(fieldName) {
        return this.requiredFields.includes(fieldName);
    }

    /** Verplicht maar nog leeg: dan het veld rood omranden. */
    isMissingField(fieldName) {
        return this.isRequiredField(fieldName)
            && !String(this.state.addressDraft?.[fieldName] ?? '').trim();
    }

    /** Een sterretje achter het label van een verplicht veld. */
    fieldLabel(fieldName, label) {
        return this.isRequiredField(fieldName) ? `${label} *` : label;
    }

    /**
     * De landenlijst ophalen, maar hooguit één keer per paginabezoek.
     * Ze verandert nooit, dus opnieuw ophalen bij elk adres is verspilling.
     */
    async ensureCountries() {
        if (this.state.countryList || this.state.countriesLoading) {
            return;
        }
        this.state.countriesLoading = true;
        try {
            const { countries } = await rpc('/event_builder/address/countries', {});
            this.state.countryList = countries;
        } finally {
            this.state.countriesLoading = false;
        }
    }

    /**
     * Selectiecontroles als methode, niet als template-uitdrukking.
     * OWL laat in templates maar een korte lijst globals toe; String() zit er
     * niet bij en zou de render laten crashen.
     */
    isCurrentCountry(countryId) {
        return String(countryId) === String(this.state.addressDraft?.country_id ?? '');
    }

    isCurrentChoice(partnerId) {
        return String(partnerId) === String(this.state.addressDraft?.partner_id ?? '');
    }

    /** De velden van een adres in het bewerkformulier zetten. */
    fillDraft(address) {
        const values = {
            partner_id: address?.id || null,
            name: address?.name || '',
            street: address?.street || '',
            street2: address?.street2 || '',
            zip: address?.zip || '',
            city: address?.city || '',
            state_id: address?.state_id || '',
            country_id: address?.country_id || '',
            phone: address?.phone || '',
            email: address?.email || '',
            vat: address?.vat || '',
        };
        this.state.addressDraft = values;
        // Een kopie als ijkpunt. Wat de klant daarna typt, vergelijken we
        // hiermee om te weten of er iets te bewaren valt.
        this.state.addressPristine = { ...values };
        // Een nieuw adres wordt altijd een onderliggend adres, dus daar mag
        // de naam wel. Bij een bestaand adres beslist de server.
        this.state.canRenameAddress = address ? !!address.can_rename : true;
        this.state.canEditVat = address ? address.can_edit_vat !== false : true;
        this.state.vatLabel = address?.vat_label || 'VAT number';
    }

    /**
     * Toont dit blok een btw-nummer?
     *
     * Alleen waar het over facturatie gaat: op het billing address, of op het
     * delivery address wanneer dat ook als billing address dienstdoet. Op een puur
     * delivery address heeft een btw-nummer niets te zoeken.
     */
    get showVat() {
        const type = this.state.expandedAddress;
        return type === 'invoice'
            || (type === 'delivery' && this.state.useDeliveryAsBilling);
    }

    get canEditVat() {
        return this.state.canEditVat !== false;
    }

    get vatLabel() {
        return this.state.vatLabel || 'VAT number';
    }

    /**
     * Mag de naam in dit formulier gewijzigd worden?
     *
     * Niet op een hoofdcontact: daar is `name` de naam van de KLANT, en die
     * hier aanpassen hernoemt hem in het hele ERP - op zijn offertes, zijn
     * facturen en in de klantenlijst. Alleen onderliggende adressen hebben
     * een naam die echt bij het adres hoort ("Feestzaal De Kring").
     */
    get canRenameAddress() {
        return this.state.canRenameAddress !== false;
    }

    /** Staat er iets in het formulier dat nog niet bewaard is? */
    get isAddressDirty() {
        const draft = this.state.addressDraft;
        const pristine = this.state.addressPristine;
        if (!draft || !pristine) {
            return false;
        }
        return Object.keys(draft).some(
            (key) => String(draft[key] ?? '') !== String(pristine[key] ?? '')
        );
    }

    /** Wijzigingen weggooien en het blok dichtklappen. */
    cancelAddressEdit() {
        this.state.expandedAddress = null;
        this.state.addressDraft = null;
        this.state.addressPristine = null;
        this.state.addressError = null;
    }

    /**
     * Een ander bestaand adres kiezen, of "Nieuw adres".
     *
     * Kiezen schrijft niets weg: we halen het gekozen adres op en vullen er
     * het formulier mee. Zouden we hier opslaan, dan zouden de velden van het
     * vórige adres op het nieuwe terechtkomen.
     */
    async onAddressChoice(ev) {
        const type = this.state.expandedAddress;
        if (ev.target.value === 'new') {
            this.fillDraft(null);
            return;
        }
        const result = await rpc('/event_builder/address/select', {
            address_type: type,
            partner_id: parseInt(ev.target.value, 10),
        });
        this.state.addresses[type] = result.address;
        this.state.addresses.states = result.states;
        this.fillDraft(result.address);
    }

    /** Bij een ander land andere provincies ophalen. */
    async onCountryChange(ev) {
        this.state.addressDraft.country_id = ev.target.value;
        this.state.addressDraft.state_id = '';
        const { states } = await rpc('/event_builder/address/states', {
            country_id: ev.target.value,
        });
        this.state.addresses.states = states;
    }

    get draftStates() {
        const countryId = parseInt(this.state.addressDraft?.country_id, 10);
        return (this.state.addresses?.states || []).filter(
            (s) => s.country_id === countryId
        );
    }

    async saveAddress() {
        this.state.addressSaving = true;
        this.state.addressError = null;
        try {
            const result = await rpc('/event_builder/address/save', {
                address_type: this.state.expandedAddress,
                partner_id: this.state.addressDraft.partner_id || null,
                values: this.state.addressDraft,
            });
            // Alleen de keuzelijst en de landgegevens overnemen. Het lever-
            // en billing address NIET: `addresses` bevat de standaardadressen van
            // de klant, en die zouden een afwijkende keuze weer wegdrukken.
            const type = this.state.expandedAddress;
            const keep = {
                delivery: this.state.addresses.delivery,
                invoice: this.state.addresses.invoice,
            };
            this.state.addresses = Object.assign(result.addresses, keep);
            // De server bepaalt wat er nog ontbreekt. Is het adres compleet,
            // dan klappen we het blok dicht; anders blijft het open met de
            // ontbrekende velden gemarkeerd.
            this.state.addresses[type] = result.address;

            // Het formulier opnieuw vullen met wat de server nu bewaart.
            // Zonder dit blijft de oude toestand hangen, met twee gevolgen:
            //
            //  - `canRenameAddress` bleef staan op de waarde van het vórige
            //    adres, dus na het aanmaken van een nieuw adres zag je nog
            //    de melding over je accountnaam.
            //  - `partner_id` bleef leeg na het aanmaken, waardoor een
            //    tweede keer bewaren nóg een adres zou aanmaken in plaats
            //    van het bestaande bij te werken.
            //
            // Het ijkpunt voor "niet opgeslagen wijzigingen" wordt hier ook
            // meteen gelijkgezet, wat klopt: net bewaard is niet gewijzigd.
            this.fillDraft(result.address);

            if (result.address.is_complete) {
                this.state.expandedAddress = null;
                this.state.addressDraft = null;
            }
        } catch (error) {
            this.state.addressError =
                error?.data?.message || _t('The address could not be saved.');
        } finally {
            this.state.addressSaving = false;
        }
    }

    /** Het bedrag dat de klant nu online betaalt om te boeken. */
    get prepaymentAmount() {
        return this.state.quote?.prepayment_amount || 0;
    }

    /** Het voorschotpercentage als geheel getal, bv. 30. */
    get prepaymentPercent() {
        return Math.round((this.state.quote?.prepayment_percent || 0) * 100);
    }

    get hasPrepayment() {
        return this.prepaymentAmount > 0
            && this.prepaymentAmount < (this.state.quote?.amount_total || 0);
    }

    /** Is de selectie zelf rond? Los van login en adressen. */
    get hasValidSelection() {
        return (
            this.state.selectedOptionIds.length > 0
            && this.missingSteps.length === 0
            && !!this.state.dateFrom
            && !!this.state.dateTo
        );
    }

    get canSubmit() {
        return (
            !this.state.submitting
            && this.hasValidSelection
            && this.state.isLoggedIn
            && this.incompleteAddresses.length === 0
            && !this.isAddressDirty
        );
    }

    /**
     * De tekst op de knop. Nooit een grijze knop zonder uitleg: hij zegt wat
     * de volgende stap is en brengt je er ook naartoe.
     */
    get submitLabel() {
        if (!this.state.isLoggedIn) {
            return _t('Sign in to book');
        }
        if (this.isAddressDirty) {
            return _t('Save your address change first');
        }
        if (this.incompleteAddresses.length) {
            return _t('Complete your %s', this.incompleteAddressLabels);
        }
        if (this.hasPrepayment) {
            return _t('Book with %s down payment', this.formatPrice(this.prepaymentAmount));
        }
        return _t('Order');
    }

    /** Klikken doet altijd iets: inloggen, adres openen, of boeken. */
    onPrimaryAction() {
        if (!this.state.isLoggedIn) {
            return this.goToLogin();
        }
        if (this.isAddressDirty) {
            // Het formulier staat al open; er hoeft alleen naartoe gescrold.
            this.goToSummary();
            return;
        }
        if (this.incompleteAddresses.length) {
            const type = this.incompleteAddresses[0];
            if (this.state.expandedAddress !== type) {
                this.toggleAddress(type);
            }
            this.goToSummary();
            return;
        }
        return this.onSubmit();
    }

    isSelected(optionId) {
        return this.state.selectedOptionIds.includes(optionId);
    }

    /** Is er in deze stap al iets gekozen? Stuurt de vinkjes in de navigatie. */
    isStepComplete(step) {
        return step.options.some((option) => this.isSelected(option.id));
    }

    /**
     * Schuif een strip een stuk op. We schuiven met 80% van de zichtbare
     * breedte in plaats van met een vast aantal pixels: zo klopt de sprong
     * op elk schermformaat, en blijft er telkens één kaartje half zichtbaar
     * als hint dat er nog meer staat.
     */
    scrollStrip(stepId, direction) {
        const stripEl = this.rootRef.el?.querySelector(`[data-strip="${stepId}"]`);
        if (!stripEl) {
            return;
        }
        stripEl.scrollBy({
            left: direction * stripEl.clientWidth * 0.8,
            behavior: 'smooth',
        });
    }

    /** Spring naar een stap vanuit de navigatie bovenaan. */
    goToStep(stepId) {
        const stepEl = this.rootRef.el?.querySelector(`[data-step="${stepId}"]`);
        stepEl?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    /**
     * Toont de vaste balk onderaan op kleine schermen. Enkel zinvol zodra er
     * iets gekozen is - een balk met een leeg totaal neemt alleen plaats in.
     */
    get showMobileBar() {
        return !!this.state.quote?.lines?.length;
    }

    /** Spring naar het prijspaneel: gebruikt door de mobiele balk. */
    goToSummary() {
        const summaryEl = this.rootRef.el?.querySelector('[data-summary]');
        summaryEl?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
            this.state.error = error?.data?.message || _t('The price could not be calculated.');
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
                option_ids: this.state.selectedOptionIds,
                delivery_id: this.address('delivery')?.id || null,
                invoice_id: this.effectiveInvoice?.id || null,
                use_delivery_as_billing: this.state.useDeliveryAsBilling,
            });
            // De server stuurt ons naar de offerte in het klantenportaal, met
            // een access_token in de URL. Daar staat de knop om het voorschot
            // te betalen.
            window.location = result.redirect_url;
        } catch (error) {
            this.state.error = error?.data?.message || _t('Something went wrong. Please try again.');
            this.state.submitting = false;
        }
    }

    /**
     * Bedragen opmaken in de valuta van de bezoeker.
     *
     * We nemen bij voorkeur de valuta uit de laatste prijsberekening, en
     * vallen terug op die van de configurator. Dat tweede is nodig om de
     * stukprijzen op de kaartjes al te tonen voordat er iets gekozen is.
     */
    formatPrice(amount) {
        const currencyId = this.state.quote?.currency_id ?? this.state.config?.currency_id;
        if (!currencyId) {
            return '';
        }
        return formatCurrency(amount, currencyId);
    }
}
