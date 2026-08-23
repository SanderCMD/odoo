# Part of the Event Builder module.
from datetime import timedelta

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError
from odoo.http import Controller, request, route


class EventBuilderController(Controller):
    """De drie webadressen waarmee de configurator in de browser praat.

    Elke `@route` is een URL. De belangrijkste argumenten:

    - type='jsonrpc' : de browser stuurt JSON en krijgt JSON terug.
                       Voor gewone HTML-pagina's gebruik je type='http'.
    - auth='public'  : ook niet-ingelogde bezoekers mogen hier binnen. Dat is
                       cruciaal: we willen geen login vragen voor een prijs.
    - website=True   : Odoo laadt de juiste website, taal en prijslijst in de
                       context vooraleer onze code draait.
    - readonly=True  : belooft dat deze route niets wegschrijft, waardoor Odoo
                       ze naar een read-only database-replica mag sturen.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_config(self, config_id=None):
        """Haal de configurator op en controleer of ze getoond mag worden.

        `sudo()` draait de query zonder rechtencontrole. Dat is hier nodig én
        veilig: nodig omdat we ook velden van producten uitlezen die een
        anonieme bezoeker niet mag zien, veilig omdat we zelf expliciet
        filteren op `active` en op de juiste website. Nooit een door de
        browser aangeleverd id blind aan sudo().browse() geven zonder zo'n
        controle erachter.
        """
        domain = [
            ('active', '=', True),
            '|',
            ('website_id', '=', False),
            ('website_id', '=', request.website.id),
        ]
        if config_id:
            # De website-beheerder koos expliciet een configurator.
            domain = [('id', '=', int(config_id))] + domain
        # Zonder expliciete keuze tonen we de eerste actieve configurator, zodat
        # een vers op de pagina gesleept blok meteen iets laat zien.
        config = request.env['event.builder.config'].sudo().search(domain, limit=1)
        if not config:
            raise UserError(_("Deze configurator bestaat niet of is niet actief."))
        return config

    def _parse_selection(self, config, guest_count, date_from, date_to, option_ids):
        """Controleer en normaliseer wat de browser doorstuurde.

        Alles wat van de client komt is verdacht tot het tegendeel bewezen is:
        de bezoeker kan de JavaScript aanpassen en om het even wat sturen.
        """
        # Dezelfde grenzen als in de browser, maar hier afgedwongen: de
        # JavaScript-controle is comfort voor de bezoeker, deze is de echte.
        guest_count = max(int(guest_count or 0), config.guest_min)
        if config.guest_max:
            guest_count = min(guest_count, config.guest_max)

        # `fields.Date.to_date` slikt zowel een string als een date-object.
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)

        if date_from and date_to and date_to < date_from:
            raise ValidationError(_("De ophaaldatum kan niet voor de leverdatum liggen."))

        if date_from and config.min_lead_days:
            earliest = fields.Date.today() + timedelta(days=config.min_lead_days)
            if date_from < earliest:
                raise ValidationError(_(
                    "Een event kan ten vroegste %(days)s dagen op voorhand geboekt worden.",
                    days=config.min_lead_days,
                ))

        option_ids = [int(option_id) for option_id in (option_ids or [])]
        days = config._get_duration_days(date_from, date_to)

        return {
            'guest_count': guest_count,
            'date_from': date_from,
            'date_to': date_to,
            'days': days,
            'option_ids': option_ids,
        }

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @route('/event_builder/config', type='jsonrpc', auth='public', website=True, readonly=True)
    def event_builder_config(self, config_id=None, **kwargs):
        """Stap 1: de configurator haalt bij het laden zijn structuur op."""
        config = self._get_config(config_id)
        return config._get_website_data()

    @route('/event_builder/price', type='jsonrpc', auth='public', website=True, readonly=True)
    def event_builder_price(
        self, config_id=None, guest_count=0, date_from=None, date_to=None, option_ids=None, **kwargs
    ):
        """Stap 2: bij elke klik de prijs herberekenen.

        Deze route wordt vaak aangeroepen (elke klik, elke wijziging van het
        aantal personen), dus de JavaScript-kant stuurt ze vertraagd
        ("debounced"). Ze schrijft niets weg: er ontstaat geen winkelmandje
        zolang de bezoeker alleen maar aan het rondkijken is.
        """
        config = self._get_config(config_id)
        selection = self._parse_selection(
            config, guest_count, date_from, date_to, option_ids)

        quote = config._get_quote(
            option_ids=selection['option_ids'],
            guest_count=selection['guest_count'],
            days=selection['days'],
            pricelist=request.pricelist,
        )
        quote['days'] = selection['days']
        return quote

    @route('/event_builder/submit', type='jsonrpc', auth='public', website=True)
    def event_builder_submit(
        self, config_id=None, guest_count=0, date_from=None, date_to=None,
        zip_code=None, option_ids=None, **kwargs
    ):
        """Stap 3: zet de selectie om in een echt winkelmandje.

        Pas hier ontstaat er een `sale.order` in de database. `request.cart`
        geeft het lopende winkelmandje van deze bezoeker terug; bestaat het
        nog niet, dan maken we er een aan. Daarna gebruiken we `_cart_add`,
        exact dezelfde methode als de gewone webshopknop, zodat kortingen,
        prijslijsten en btw identiek behandeld worden.
        """
        config = self._get_config(config_id)
        selection = self._parse_selection(
            config, guest_count, date_from, date_to, option_ids)

        line_values = config._get_line_values(
            selection['option_ids'], selection['guest_count'], selection['days'])
        if not line_values:
            raise ValidationError(_("Selecteer minstens één optie."))

        order_sudo = request.cart or request.website._create_cart()

        # We beginnen met een leeg mandje: een event is één geheel, geen
        # verzameling losse artikelen die je stap voor stap bijeenraapt.
        order_sudo.order_line.unlink()

        order_sudo.write({
            'event_builder_config_id': config.id,
            'event_guest_count': selection['guest_count'],
            'event_delivery_date': selection['date_from'],
            'event_pickup_date': selection['date_to'],
            'event_zip': zip_code,
        })

        for line in line_values:
            order_sudo._cart_add(
                product_id=line['product_id'],
                quantity=line['product_uom_qty'],
            )

        return {
            'order_id': order_sudo.id,
            'redirect_url': '/shop/cart',
        }
