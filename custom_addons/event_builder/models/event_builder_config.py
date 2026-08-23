# Part of the Event Builder module.
from odoo import Command, api, fields, models


class EventBuilderConfig(models.Model):
    """Een volledige configurator-pagina, bv. "Bedrijfsevent" of "Trouwfeest".

    Je kan er meerdere hebben: elk met eigen stappen, eigen opties en een
    eigen snippet op de website.
    """

    # _name registreert een nieuwe tabel in de database. Odoo maakt hier
    # automatisch de tabel `event_builder_config` van (punten worden underscores).
    _name = 'event.builder.config'

    # Verschijnt in foutmeldingen en in de UI. Altijd invullen.
    _description = "Event Builder Configuration"

    # Standaard sorteervolgorde bij het ophalen van records.
    _order = 'sequence, id'

    name = fields.Char(string="Naam", required=True, translate=True)

    # `sequence` is een Odoo-conventie: een integer waarop gesorteerd wordt en
    # die de gebruiker in lijstweergaven kan verslepen met het handvat.
    sequence = fields.Integer(default=10)

    # `active` is eveneens een conventie. Zodra dit veld bestaat, verbergt Odoo
    # records met active=False overal automatisch (soft delete / archiveren).
    active = fields.Boolean(default=True)

    website_id = fields.Many2one(
        comodel_name='website',
        string="Website",
        ondelete='cascade',
        help="Laat leeg om deze configurator op alle websites te tonen.",
    )

    # --- Logistiek: wat we aan de bezoeker vragen vooraleer hij kiest ---

    guest_label = fields.Char(
        string="Label aantal personen", default="Aantal personen", translate=True)
    guest_min = fields.Integer(string="Minimum personen", default=10)
    guest_max = fields.Integer(string="Maximum personen", default=1000)
    guest_default = fields.Integer(string="Standaard personen", default=50)

    min_lead_days = fields.Integer(
        string="Minimale doorlooptijd (dagen)",
        default=7,
        help="Hoeveel dagen op voorhand moet een event minstens geboekt worden?",
    )

    # --- Relaties ---

    # One2many is de "omgekeerde" kant van een Many2one. Het tweede argument
    # ('config_id') is de naam van het Many2one-veld op het andere model dat
    # naar hier terugwijst. Er wordt geen kolom aangemaakt in deze tabel.
    step_ids = fields.One2many(
        comodel_name='event.builder.step',
        inverse_name='config_id',
        string="Stappen",
    )

    # Een computed field: niet opgeslagen in de database, maar berekend bij
    # het uitlezen. `store=True` zou het wel opslaan (en indexeerbaar maken).
    step_count = fields.Integer(string="Aantal stappen", compute='_compute_step_count')

    # `@api.depends` vertelt Odoo wanneer deze berekening hergedaan moet worden:
    # telkens als `step_ids` verandert.
    @api.depends('step_ids')
    def _compute_step_count(self):
        # `self` is hier GEEN enkel record, maar een *recordset*: een lijst van
        # records van dit model. Een compute-methode krijgt er potentieel
        # honderden tegelijk, dus je loopt er altijd over.
        for config in self:
            # `config` is dan wel een recordset van precies 1 record.
            # Toekennen aan een veld schrijft de berekende waarde weg.
            config.step_count = len(config.step_ids)

    # ------------------------------------------------------------------
    # Business logic: van een keuze op de website naar offertelijnen
    # ------------------------------------------------------------------
    # Deze methodes staan bewust op het MODEL en niet in de controller.
    # De controller is enkel de "deurmat" naar het web; de logica hier is
    # herbruikbaar vanuit de backend, vanuit tests en vanuit een toekomstige
    # API, en kan door een andere module aangepast worden via _inherit.

    def _get_website_data(self):
        """Bouw de structuur die de configurator in de browser nodig heeft."""
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'guest': {
                'label': self.guest_label,
                'min': self.guest_min,
                'max': self.guest_max,
                'default': self.guest_default,
            },
            'min_lead_days': self.min_lead_days,
            'steps': [{
                'id': step.id,
                'name': step.name,
                'description': step.description or '',
                'selection_type': step.selection_type,
                'is_required': step.is_required,
                'options': [{
                    'id': option.id,
                    'name': option.name,
                    'description': option.description or '',
                    'image_url': option._get_image_url(),
                    'qty_mode': option.qty_mode,
                    'charge_per_day': option.charge_per_day,
                } for option in step.option_ids],
            } for step in self.step_ids],
        }

    def _get_duration_days(self, date_from, date_to):
        """Aantal dagen tussen twee datums, minimaal 1."""
        if not date_from or not date_to:
            return 1
        return max((date_to - date_from).days, 1)

    def _get_line_values(self, option_ids, guest_count, days):
        """Zet de keuze van de bezoeker om in waarden voor offertelijnen.

        :param list option_ids: ids van de aangeklikte `event.builder.option`
        :param int guest_count: aantal personen
        :param int days: duur in dagen
        :return: lijst van dicts, klaar om een `sale.order.line` mee te maken
        :rtype: list[dict]
        """
        self.ensure_one()

        # We halen de opties op via `search` in plaats van via `browse` op de
        # doorgestuurde ids, zodat een bezoeker die zelf ids verzint alleen
        # opties uit DEZE configurator kan selecteren. Nooit blind vertrouwen
        # op wat de browser stuurt.
        options = self.env['event.builder.option'].sudo().search([
            ('id', 'in', option_ids),
            ('config_id', '=', self.id),
        ])

        values = []
        for option in options:
            quantity = option._get_quantity(guest_count, days)
            if quantity <= 0:
                continue
            values.append({
                'option_id': option.id,
                'product_id': option.product_id.id,
                'name': option.name,
                'product_uom_qty': quantity,
            })
        return values

    def _get_quote(self, option_ids, guest_count, days, pricelist=None, partner=None):
        """Bereken de prijs van een selectie, zonder iets weg te schrijven.

        De truc: we maken met `new()` een sale.order die alleen in het
        geheugen bestaat. Odoo berekent daar exact dezelfde prijzen, kortingen
        en btw op als op een echte offerte, maar er komt geen enkele rij in de
        database terecht. Zo kan de prijs op de website per definitie niet
        afwijken van de prijs op de uiteindelijke offerte.
        """
        self.ensure_one()

        partner = partner or self.env.user.partner_id
        line_values = self._get_line_values(option_ids, guest_count, days)

        if not line_values:
            currency = pricelist.currency_id if pricelist else self.env.company.currency_id
            return {
                'lines': [],
                'amount_untaxed': 0.0,
                'amount_tax': 0.0,
                'amount_total': 0.0,
                'currency_id': currency.id,
            }

        order_values = {
            'partner_id': partner.id,
            'date_order': fields.Datetime.now(),
            'order_line': [
                Command.create({
                    'product_id': line['product_id'],
                    'product_uom_qty': line['product_uom_qty'],
                })
                for line in line_values
            ],
        }
        if pricelist:
            order_values['pricelist_id'] = pricelist.id

        order = self.env['sale.order'].sudo().new(order_values)

        # De virtuele lijnen komen in dezelfde volgorde terug als we ze
        # aanmaakten, dus we kunnen ze één op één koppelen aan `line_values`.
        for line_value, order_line in zip(line_values, order.order_line):
            line_value.update({
                'price_unit': order_line.price_unit,
                'price_subtotal': order_line.price_subtotal,
                'price_total': order_line.price_total,
            })

        return {
            'lines': line_values,
            'amount_untaxed': order.amount_untaxed,
            'amount_tax': order.amount_tax,
            'amount_total': order.amount_total,
            'currency_id': order.currency_id.id,
        }
