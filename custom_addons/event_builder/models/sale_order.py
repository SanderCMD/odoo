# Part of the Event Builder module.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    """Uitbreiding van het bestaande offerte/order-model.

    `_inherit` zonder `_name` betekent: voeg velden en gedrag toe aan het
    BESTAANDE model `sale.order`. Er komt geen nieuwe tabel bij; de kolommen
    worden toegevoegd aan de bestaande `sale_order`-tabel. Dit is de normale
    manier om Odoo uit te breiden zonder core-code aan te passen.
    """

    _inherit = 'sale.order'

    # --- Logistiek zoals de klant het in de configurator invult ---

    event_delivery_date = fields.Date(string="Leverdatum")
    event_pickup_date = fields.Date(string="Ophaaldatum")
    event_guest_count = fields.Integer(string="Aantal personen")
    event_zip = fields.Char(string="Postcode leverlocatie")
    event_builder_config_id = fields.Many2one(
        comodel_name='event.builder.config',
        string="Samengesteld via",
        ondelete='set null',
    )

    event_duration_days = fields.Integer(
        string="Duur (dagen)", compute='_compute_event_duration_days')

    # --- Opvolging van het voorschot ---
    #
    # Odoo heeft `amount_paid` (som van de geslaagde online transacties), maar
    # toont nergens in één oogopslag "hoeveel verwachtten we, en hoeveel staat
    # er nog open". Voor een eventbedrijf dat maanden vooraf boekt, is dat net
    # de vraag die je elke dag stelt.

    event_prepayment_amount = fields.Monetary(
        string="Verwacht voorschot",
        compute='_compute_event_payment_amounts',
        help="Het bedrag dat de klant online moet betalen om de boeking te "
             "bevestigen.",
    )
    event_amount_due = fields.Monetary(
        string="Nog te betalen",
        compute='_compute_event_payment_amounts',
        help="Totaalbedrag min wat er al online betaald werd.",
    )

    @api.depends('amount_total', 'amount_paid', 'require_payment', 'prepayment_percent')
    def _compute_event_payment_amounts(self):
        for order in self:
            # We hergebruiken Odoo's eigen berekening in plaats van zelf
            # amount_total * percent te doen, zodat onze cijfers nooit een cent
            # afwijken van wat het klantenportaal aanrekent.
            order.event_prepayment_amount = order._get_prepayment_required_amount()
            order.event_amount_due = order.currency_id.round(
                order.amount_total - order.amount_paid
            )

    @api.depends('event_delivery_date', 'event_pickup_date')
    def _compute_event_duration_days(self):
        for order in self:
            if order.event_delivery_date and order.event_pickup_date:
                delta = order.event_pickup_date - order.event_delivery_date
                # Levering en ophaling op dezelfde dag telt als 1 dag.
                order.event_duration_days = max(delta.days, 1)
            else:
                order.event_duration_days = 1

    # --- Koppeling met de standaard leverdatum van Odoo ---
    #
    # Odoo heeft al een veld `commitment_date` ("Delivery Date"). Dat stuurt de
    # leveringsbonnen en de planning aan (zie sale_stock). We verzinnen dus
    # geen tweede waarheid, maar houden het gelijk met onze leverdatum.
    #
    # Dat doen we door create() en write() te overschrijven in plaats van met
    # een computed field. Een computed field zou hier moeten teruglezen wat de
    # gebruiker eventueel manueel invulde, en een veld uitlezen binnen zijn
    # eigen compute is vragen om problemen. Deze aanpak is expliciet: we
    # schrijven de waarde alleen mee als de aanroeper ze niet zelf meegeeft,
    # zodat een medewerker de leverdatum in de backend nog kan bijsturen.

    @api.model_create_multi
    def create(self, vals_list):
        # `@api.model_create_multi` betekent dat create() een LIJST van dicts
        # krijgt, niet één dict. Odoo bundelt aanmaakoperaties voor snelheid.
        for vals in vals_list:
            self._sync_commitment_date(vals)
        # `super()` roept de originele Odoo-implementatie op. Vergeet dit nooit,
        # anders wordt het record simpelweg niet aangemaakt.
        return super().create(vals_list)

    def write(self, vals):
        self._sync_commitment_date(vals)
        return super().write(vals)

    @staticmethod
    def _sync_commitment_date(vals):
        """Vul commitment_date aan op basis van de leverdatum van het event."""
        if vals.get('event_delivery_date') and not vals.get('commitment_date'):
            vals['commitment_date'] = fields.Datetime.to_datetime(
                vals['event_delivery_date'])

    # `@api.constrains` draait bij elke create/write op de vermelde velden en
    # mag de opslag blokkeren met een ValidationError. Dit is de laatste
    # verdedigingslinie: ook wie de website omzeilt en rechtstreeks naar de
    # API schrijft, botst hierop.
    @api.constrains('event_delivery_date', 'event_pickup_date')
    def _check_event_dates(self):
        for order in self:
            if (
                order.event_delivery_date
                and order.event_pickup_date
                and order.event_pickup_date < order.event_delivery_date
            ):
                # `_()` markeert de tekst als vertaalbaar.
                raise ValidationError(_(
                    "De ophaaldatum kan niet voor de leverdatum liggen."
                ))
