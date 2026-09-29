# Part of the Event Builder module.
from datetime import timedelta

from odoo import _, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request, route

from odoo.addons.portal.controllers.portal import CustomerPortal


# We erven van CustomerPortal in plaats van van Controller. Niet voor de
# routes, maar voor de adresregels: `_get_mandatory_billing_address_fields` en
# `_get_mandatory_delivery_address_fields` staan daar. Zo gebruikt de
# configurator exact dezelfde verplichte velden als het klantenportaal en de
# webshop, inclusief de landafhankelijke uitzonderingen. website_sale doet
# hetzelfde (WebsiteSale erft via PaymentPortal van CustomerPortal).
class EventBuilderController(CustomerPortal):
    """De webadressen waarmee de configurator in de browser praat.

    Elke `@route` is een URL. De belangrijkste argumenten:

    - type='jsonrpc' : de browser stuurt JSON en krijgt JSON terug.
                       Voor gewone HTML-pagina's gebruik je type='http'.
    - auth='public'  : ook niet-ingelogde bezoekers mogen hier binnen.
      auth='user'    : enkel ingelogde klanten. Kijken en prijzen berekenen is
                       publiek; boeken en adressen beheren niet.
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
            raise UserError(_("This configurator does not exist or is not active."))
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
            raise ValidationError(_("The pickup date cannot be before the delivery date."))

        if date_from and config.min_lead_days:
            earliest = fields.Date.today() + timedelta(days=config.min_lead_days)
            if date_from < earliest:
                raise ValidationError(_(
                    "An event can be booked at the earliest %(days)s days in advance.",
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
    # Routes: structuur en prijs (publiek)
    # ------------------------------------------------------------------

    @route('/event_builder/config', type='jsonrpc', auth='public', website=True, readonly=True)
    def event_builder_config(self, config_id=None, **kwargs):
        """De configurator haalt bij het laden zijn structuur op."""
        config = self._get_config(config_id)
        data = config._get_website_data(pricelist=request.pricelist)
        # De browser moet weten of hij de boekknop of een loginknop toont.
        data['is_logged_in'] = not request.env.user._is_public()
        return data

    @route('/event_builder/price', type='jsonrpc', auth='public', website=True, readonly=True)
    def event_builder_price(
        self, config_id=None, guest_count=0, date_from=None, date_to=None, option_ids=None, **kwargs
    ):
        """Bij elke klik de prijs herberekenen.

        Deze route wordt vaak aangeroepen (elke klik, elke wijziging van het
        aantal personen), dus de JavaScript-kant stuurt ze vertraagd
        ("debounced"). Ze schrijft niets weg: er ontstaat geen offerte zolang
        de bezoeker alleen maar aan het rondkijken is.
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

    # ------------------------------------------------------------------
    # Adressen (enkel ingelogd)
    # ------------------------------------------------------------------

    def _eb_mandatory_fields(self, address_type, country_sudo):
        """De verplichte velden voor dit adrestype, volgens Odoo zelf.

        Deze twee methodes komen uit CustomerPortal en houden rekening met het
        land: België vraagt geen provincie, de Verenigde Staten wel. Zelf een
        lijstje bijhouden zou binnen een maand afwijken van de rest van Odoo.
        """
        if address_type == 'invoice':
            field_names = self._get_mandatory_billing_address_fields(country_sudo)
        else:
            field_names = self._get_mandatory_delivery_address_fields(country_sudo)

        # De postcode is bij ons ALTIJD verplicht, ook in landen waar Odoo
        # hem optioneel vindt (`country.zip_required` staat voor Belgie uit).
        # Wij hebben hem nodig om te bepalen of we ergens kunnen leveren en
        # straks wat het transport kost.
        #
        # Bewust hier en niet door `_get_mandatory_address_fields` te
        # overschrijven: die methode is van CustomerPortal, en een override
        # zou de postcode ook verplicht maken in het klantenportaal en de
        # webshop-checkout. Deze regel geldt alleen voor de Event Builder.
        return field_names | {'zip'}

    def _eb_allowed_addresses(self):
        """De adressen die deze ingelogde klant mag kiezen of bewerken.

        Elke adres-id die de browser doorstuurt, wordt hiertegen gecontroleerd.
        Zonder die controle zou iemand met aangepaste JavaScript een
        willekeurig contact-id kunnen opgeven en zo andermans adres uitlezen
        of overschrijven.
        """
        partner_sudo = request.env.user.partner_id.sudo()
        return request.env['res.partner'].sudo()._eb_selectable_addresses(partner_sudo)

    def _eb_check_address(self, partner_id):
        """Zet een doorgestuurd adres-id om in een record, of weiger het."""
        allowed = self._eb_allowed_addresses()
        partner_sudo = allowed.filtered(lambda p: p.id == int(partner_id))
        if not partner_sudo:
            raise AccessError(_("This address does not belong to your account."))
        return partner_sudo

    def _eb_address_payload(self, partner_sudo, address_type):
        """Een adres plus de velden die er nog aan ontbreken."""
        data = partner_sudo._eb_address_data()
        mandatory = self._eb_mandatory_fields(address_type, partner_sudo.country_id)
        # `mandatory` gaat mee naar de browser zodat het formulier kan tonen
        # welke velden verplicht zijn. Zonder die lijst raadt de klant waarom
        # de boekknop niet meewerkt.
        data['mandatory'] = sorted(mandatory)
        data['missing'] = sorted(f for f in mandatory if not partner_sudo[f])
        data['is_complete'] = not data['missing']

        # Alleen onderliggende adressen mogen hernoemd worden. Op een
        # hoofdcontact - de klant zelf of zijn bedrijf - is `name` de naam van
        # de KLANT, en die aanpassen vanuit een adresformulier hernoemt hem in
        # het hele ERP: op zijn offertes, facturen en in de klantenlijst.
        data['can_rename'] = bool(partner_sudo.parent_id)

        # --- VAT number ---
        #
        # `vat` is in Odoo een "commercial field": het hoort bij de juridische
        # entiteit, niet bij een los adres. Zie res_partner._synced_commercial_fields:
        # wijzig je het op een kindadres, dan propageert Odoo het naar de
        # commerciele partner. Een ander btw-nummer per billing address bestaat
        # dus niet, en dat is juist - het nummer hoort bij het bedrijf.
        #
        # We tonen het daarom als "het btw-nummer van je account" en schrijven
        # het ook daarheen, in plaats van te doen alsof het per adres kan.
        commercial_sudo = partner_sudo.commercial_partner_id
        data['vat'] = commercial_sudo.vat or ''
        # Het land bepaalt hoe het nummer heet: "BTW" hier, "VAT" elders,
        # "SIRET" in Frankrijk. Odoo houdt dat bij op res.country.
        data['vat_label'] = request.env.company.country_id.vat_label or _("VAT number")
        # Odoo blokkeert het wijzigen zodra er facturen uitgestuurd zijn
        # (account/models/partner.py, can_edit_vat).
        data['can_edit_vat'] = (
            commercial_sudo.can_edit_vat()
            if hasattr(commercial_sudo, 'can_edit_vat') else True
        )
        return data

    def _eb_states(self, countries_sudo):
        """De provincies of staten van de opgegeven landen."""
        if not countries_sudo:
            return []
        states = request.env['res.country.state'].sudo().search([
            ('country_id', 'in', countries_sudo.ids),
        ])
        return [{
            'id': state.id,
            'name': state.display_name,
            'country_id': state.country_id.id,
        } for state in states]

    def _eb_addresses_payload(self):
        """Alles wat de adreskiezer nodig heeft voor de ingelogde klant."""
        Partner = request.env['res.partner'].sudo()
        partner_sudo = request.env.user.partner_id.sudo()

        # `address_get` doorzoekt de onderliggende adressen naar het gevraagde
        # type en valt terug op de klant zelf. Heeft de klant een echt
        # billing address, dan krijgt hij dat voorgesteld; heeft hij er geen, dan
        # zijn eigen contact.
        defaults = partner_sudo.address_get(['delivery', 'invoice'])
        delivery_sudo = Partner.browse(defaults.get('delivery') or partner_sudo.id)
        invoice_sudo = Partner.browse(defaults.get('invoice') or partner_sudo.id)

        return {
            'delivery': self._eb_address_payload(delivery_sudo, 'delivery'),
            'invoice': self._eb_address_payload(invoice_sudo, 'invoice'),
            # Standaard naar hetzelfde adres factureren, TENZIJ de klant een
            # apart billing address heeft staan. Dan respecteren we die keuze.
            'use_delivery_as_billing': invoice_sudo.id == delivery_sudo.id,
            'choices': [{
                'id': address.id,
                'label': address.display_name,
                'display': address._eb_address_line(),
            } for address in self._eb_allowed_addresses()],
            'states': self._eb_states(delivery_sudo.country_id | invoice_sudo.country_id),
        }

    @route('/event_builder/address/countries', type='jsonrpc', auth='user', website=True, readonly=True)
    def event_builder_address_countries(self, **kwargs):
        """De landenlijst, apart en pas op aanvraag.

        Bewust niet meegestuurd met elk adresantwoord: de lijst verandert
        nooit, terwijl adressen bij elke wijziging opnieuw opgehaald worden.
        De browser haalt ze één keer op zodra iemand een adresformulier
        openklapt, en houdt ze daarna bij. Wie nooit een adres bewerkt,
        downloadt ze dus ook nooit.
        """
        return {'countries': [{
            'id': country.id,
            'name': country.display_name,
            'state_required': country.state_required,
            'zip_required': country.zip_required,
        } for country in request.env['res.country'].sudo().search([])]}

    @route('/event_builder/addresses', type='jsonrpc', auth='user', website=True, readonly=True)
    def event_builder_addresses(self, **kwargs):
        """Het lever- en billing address van de ingelogde klant ophalen."""
        return self._eb_addresses_payload()

    @route('/event_builder/address/select', type='jsonrpc', auth='user', website=True, readonly=True)
    def event_builder_address_select(self, address_type, partner_id, **kwargs):
        """Een ander bestaand adres kiezen, zonder iets te wijzigen.

        Bewust een eigen route en niet /address/save: die zou de waarden uit
        het formulier wegschrijven, en dat zijn bij een wissel nog die van het
        vórige adres. Kiezen is lezen, geen schrijven.
        """
        if address_type not in ('delivery', 'invoice'):
            raise ValidationError(_("Unknown address type."))
        partner_sudo = self._eb_check_address(partner_id)
        return {
            'address': self._eb_address_payload(partner_sudo, address_type),
            'states': self._eb_states(partner_sudo.country_id),
        }

    @route('/event_builder/address/states', type='jsonrpc', auth='user', website=True, readonly=True)
    def event_builder_address_states(self, country_id, **kwargs):
        """De provincies van een land, opgehaald zodra de klant het land wijzigt."""
        country_sudo = request.env['res.country'].sudo().browse(int(country_id)).exists()
        return {'states': self._eb_states(country_sudo)}

    def _eb_save_vat(self, partner_sudo, vat):
        """Het btw-nummer wegschrijven op de commerciele partner.

        Niet op het adres zelf: `vat` staat in `_synced_commercial_fields`
        (odoo/addons/base/models/res_partner.py), wat betekent dat Odoo een
        wijziging op een kindadres tóch doorschuift naar de commerciele
        entiteit. Eén nummer per bedrijf, niet per adres - en dat klopt, want
        een btw-nummer hoort bij de rechtspersoon.

        Wij schrijven het meteen op de juiste plek, zodat het gedrag
        voorspelbaar is in plaats van dat Odoo het achteraf verplaatst.
        """
        if not vat:
            return

        commercial_sudo = partner_sudo.commercial_partner_id
        if commercial_sudo.vat == vat:
            return

        # Odoo weigert een wijziging zodra er facturen uitgestuurd zijn.
        if hasattr(commercial_sudo, 'can_edit_vat') and not commercial_sudo.can_edit_vat():
            raise ValidationError(_(
                "Your VAT number can no longer be changed because invoices"
                " have already been issued. Please contact us about this."
            ))

        # De controle van Odoo zelf gebruiken (zit in de module `account`),
        # zodat een ongeldig nummer hier dezelfde foutmelding geeft als in de
        # rest van het systeem.
        commercial_sudo.write({'vat': vat})
        if hasattr(commercial_sudo, '_check_vat'):
            commercial_sudo._check_vat()

    @route('/event_builder/address/save', type='jsonrpc', auth='user', website=True)
    def event_builder_address_save(self, address_type, values=None, partner_id=None, **kwargs):
        """Een adres aanvullen of een nieuw adres aanmaken.

        Zonder `partner_id` maken we een NIEUW adres onder de klant, met het
        juiste type. Het hoofdcontact blijft dan ongemoeid, wat vaak klopt: het
        delivery address van een event is een zaal of een weide, geen bedrijfsadres.
        """
        if address_type not in ('delivery', 'invoice'):
            raise ValidationError(_("Unknown address type."))

        Partner = request.env['res.partner'].sudo()

        # Enkel velden die de adreskiezer kent. Zo kan aangepaste JavaScript
        # geen andere velden meeschrijven, bijvoorbeeld `user_ids`.
        allowed_fields = set(Partner.EB_ADDRESS_FIELDS)
        vals = {k: v for k, v in (values or {}).items() if k in allowed_fields}
        for fname in ('state_id', 'country_id'):
            if fname in vals:
                vals[fname] = int(vals[fname]) if vals[fname] else False

        # Het btw-nummer hoort bij de juridische entiteit, niet bij een adres.
        # We halen het uit de gewone adreswaarden en behandelen het apart.
        vat = (vals.pop('vat', None) or '').strip()

        if partner_id:
            partner_sudo = self._eb_check_address(partner_id)
            if not partner_sudo.parent_id:
                # Een hoofdcontact: hier is `name` de naam van de klant zelf.
                # Die laten we met rust, hoe de browser het ook aanlevert.
                # De adresvelden mag hij wel aanvullen.
                vals.pop('name', None)
            partner_sudo.write(vals)
        else:
            commercial_sudo = request.env.user.partner_id.sudo().commercial_partner_id
            partner_sudo = Partner.create(dict(
                vals,
                type=address_type,
                parent_id=commercial_sudo.id,
            ))

        self._eb_save_vat(partner_sudo, vat)

        # Pas na het opslaan controleren, met het land zoals het nu is: een
        # klant die van België naar de VS wisselt, heeft plots een staat nodig.
        return {
            'address': self._eb_address_payload(partner_sudo, address_type),
            'addresses': self._eb_addresses_payload(),
        }

    # ------------------------------------------------------------------
    # Boeken (enkel ingelogd)
    # ------------------------------------------------------------------

    @route('/event_builder/submit', type='jsonrpc', auth='user', website=True)
    def event_builder_submit(
        self, config_id=None, guest_count=0, date_from=None, date_to=None,
        zip_code=None, option_ids=None,
        delivery_id=None, invoice_id=None, use_delivery_as_billing=True,
        **kwargs
    ):
        """Zet de selectie om in een offerte en stuur de klant erheen.

        `auth='user'` in plaats van 'public': enkel ingelogde klanten kunnen
        boeken. Kijken en prijzen berekenen blijft publiek, want dat is het
        verkoopargument van het platform.

        De klant komt terecht op zijn eigen offerte in het klantenportaal,
        waar Odoo zonder verdere code de knop toont om het voorschot te
        betalen.
        """
        config = self._get_config(config_id)
        selection = self._parse_selection(
            config, guest_count, date_from, date_to, option_ids)

        partner_sudo = request.env.user.partner_id.sudo()
        delivery_sudo = self._eb_check_address(delivery_id) if delivery_id else partner_sudo
        if use_delivery_as_billing or not invoice_id:
            invoice_sudo = delivery_sudo
        else:
            invoice_sudo = self._eb_check_address(invoice_id)

        # De controle uit de browser nog eens overdoen. Wie de knop met
        # aangepaste JavaScript toch indrukt, botst hierop.
        for address_sudo, address_type, label in (
            (delivery_sudo, 'delivery', _("delivery address")),
            (invoice_sudo, 'invoice', _("billing address")),
        ):
            missing = [
                fname
                for fname in self._eb_mandatory_fields(address_type, address_sudo.country_id)
                if not address_sudo[fname]
            ]
            if missing:
                raise ValidationError(_("Complete your %(label)s before booking.", label=label))

        order_sudo = config._create_quotation(
            partner=partner_sudo,
            option_ids=selection['option_ids'],
            guest_count=selection['guest_count'],
            days=selection['days'],
            logistics={
                'date_from': selection['date_from'],
                'date_to': selection['date_to'],
                # De postcode komt uit het delivery address. De configurator vraagt
                # ze niet meer apart: daar zou ze kunnen afwijken van het adres
                # waar we effectief leveren. Wordt later de basis voor
                # leverzones en transportkosten.
                'zip_code': delivery_sudo.zip or zip_code,
            },
            delivery=delivery_sudo,
            invoice=invoice_sudo,
        )

        return {
            'order_id': order_sudo.id,
            # get_portal_url() zet het access_token er zelf in.
            'redirect_url': order_sudo.get_portal_url(),
        }
