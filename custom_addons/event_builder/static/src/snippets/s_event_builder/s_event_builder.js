import { Interaction } from '@web/public/interaction';
import { registry } from '@web/core/registry';

import { EventBuilder } from '@event_builder/js/builder/event_builder';

/**
 * De brug tussen het website-snippet en de OWL-component.
 *
 * Odoo 19 gebruikt hiervoor het "Interaction"-framework (de opvolger van het
 * oude publicWidget). Het werkt zo:
 *
 * - `static selector` is een CSS-selector. Odoo zoekt op elke publieke pagina
 *   naar elementen die eraan voldoen en maakt per match één instantie aan.
 * - `this.el` is dan dat gevonden HTML-element.
 * - `this.mountComponent(...)` hangt er een OWL-component onder, en ruimt die
 *   automatisch weer op wanneer de bezoeker naar een andere pagina gaat of
 *   wanneer de website-editor het blok verwijdert.
 *
 * Deze laag is bewust dun: alle echte logica zit in de component zelf.
 */
export class EventBuilderSnippet extends Interaction {
    static selector = '.s_event_builder';

    setup() {
        // De website-beheerder stelt in de editor in wélke configurator dit
        // blok toont. Die keuze leeft in het data-attribuut op het snippet.
        // 0 = 'geen expliciete keuze'; de server valt dan terug op de
        // eerste actieve configurator.
        this.configId = parseInt(this.el.dataset.configId, 10) || 0;
        this.mountEl = this.el.querySelector('.o_event_builder_mount');
    }

    start() {
        if (!this.mountEl) {
            return;
        }
        // De tijdelijke inhoud uit het snippet weghalen; vanaf hier neemt de
        // OWL-component het over. We doen dit pas in start() en niet in de
        // template, zodat het blok in de editor en bij een trage verbinding
        // toch iets toont.
        this.el.querySelector('.o_event_builder_placeholder')?.remove();

        this.mountComponent(this.mountEl, EventBuilder, {
            configId: this.configId,
        });
    }
}

// Registreren in de registry: dit is hoe Odoo modules laat samenwerken zonder
// dat de ene de andere hoeft te kennen. De sleutel is per conventie
// "<module>.<naam>" zodat hij uniek blijft over alle modules heen.
registry
    .category('public.interactions')
    .add('event_builder.event_builder_snippet', EventBuilderSnippet);
