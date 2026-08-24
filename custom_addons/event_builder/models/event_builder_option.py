# Part of the Event Builder module.
import math

from odoo import _, api, fields, models


class EventBuilderOption(models.Model):
    """Eén aanklikbare keuze binnen een stap, gekoppeld aan een echt product.

    Dit is het scharnierpunt van de hele module: de configurator toont
    *opties*, maar de offerte bevat *producten*. Daardoor blijven voorraad,
    inkoop, marge en facturatie gewoon werken zoals in standaard Odoo.
    """

    _name = 'event.builder.option'
    _description = "Event Builder Option"
    _order = 'sequence, id'

    step_id = fields.Many2one(
        comodel_name='event.builder.step',
        string="Stap",
        required=True,
        ondelete='cascade',
        index=True,
    )

    # `related` haalt een waarde op via een andere relatie ("de config van mijn
    # stap"). `store=True` schrijft ze mee weg, zodat je erop kan zoeken/filteren.
    config_id = fields.Many2one(
        related='step_id.config_id', store=True, index=True)

    product_id = fields.Many2one(
        comodel_name='product.product',
        string="Product",
        required=True,
        # `domain` beperkt wat de gebruiker mag kiezen in de dropdown.
        # Enkel producten die verkoopbaar zijn, en geen combo-producten.
        domain=[('sale_ok', '=', True), ('type', '!=', 'combo')],
        ondelete='restrict',
    )

    # Standaard de productnaam, maar overschrijfbaar: op de website wil je
    # misschien "Rustiek houten banket" i.p.v. "TAFEL-HOUT-240".
    name = fields.Char(
        string="Titel op website",
        compute='_compute_name',
        store=True,
        readonly=False,   # readonly=False maakt van een compute een *default*
        translate=True,
    )

    sequence = fields.Integer(default=10)
    description = fields.Text(string="Korte omschrijving", translate=True)
    image = fields.Image(
        string="Afbeelding", max_width=1024, max_height=1024,
        help="Laat leeg om de productafbeelding te gebruiken.")

    # --- Hoeveelheidslogica: het hart van de prijsberekening ---

    qty_mode = fields.Selection(
        selection=[
            ('fixed', "Vast aantal"),
            ('per_person', "Per persoon"),
            ('per_x', "Per X personen"),
        ],
        string="Hoeveelheid",
        default='per_person',
        required=True,
    )

    qty_factor = fields.Float(
        string="Factor",
        default=1.0,
        digits=(12, 2),
        help="Vast aantal: het aantal stuks.\n"
             "Per persoon: aantal stuks per persoon (1 stoel = 1).\n"
             "Per X personen: aantal personen per stuk (1 tafel per 8 = 8).",
    )

    charge_per_day = fields.Boolean(
        string="Per dag aanrekenen",
        help="Vermenigvuldigt de hoeveelheid met de duur van het event "
             "(ophaaldatum - leverdatum). Aanvinken voor huurmateriaal, "
             "uitvinken voor catering en diensten.",
    )

    # Handige leesvelden voor in de backend-lijst, zodat je meteen de prijs ziet.
    currency_id = fields.Many2one(related='product_id.currency_id')
    list_price = fields.Float(
        related='product_id.lst_price', string="Catalogusprijs")

    @api.depends('product_id')
    def _compute_name(self):
        for option in self:
            # `or option.name` zorgt dat een handmatig ingevulde titel niet
            # overschreven wordt wanneer er nog geen product gekozen is.
            option.name = option.product_id.display_name or option.name

    def _get_quantity(self, guest_count, days):
        """Bereken hoeveel stuks van dit product nodig zijn.

        :param int guest_count: aantal personen dat de bezoeker invulde
        :param int days: duur van het event in dagen (minimaal 1)
        :return: de hoeveelheid voor op de offertelijn
        :rtype: float
        """
        # `ensure_one()` gooit een duidelijke fout als `self` per ongeluk meer
        # dan één record bevat. Standaardreflex bij methodes die één record
        # veronderstellen.
        self.ensure_one()

        if self.qty_mode == 'fixed':
            qty = self.qty_factor
        elif self.qty_mode == 'per_person':
            qty = guest_count * self.qty_factor
        elif self.qty_mode == 'per_x' and self.qty_factor:
            # Naar boven afronden: 150 personen met 8 per tafel = 19 tafels.
            qty = math.ceil(guest_count / self.qty_factor)
        else:
            qty = 0.0

        if self.charge_per_day:
            qty *= max(days, 1)

        return qty

    def _get_website_data(self, pricelist=None):
        """De gegevens die één kaartje in de configurator nodig heeft."""
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'image_url': self._get_image_url(),
            'qty_mode': self.qty_mode,
            'charge_per_day': self.charge_per_day,
            'unit_price': self._get_unit_price(pricelist),
            'price_label': self._get_price_label(),
        }

    def _get_unit_price(self, pricelist=None):
        """Stukprijs volgens de prijslijst van de bezoeker, btw zoals ingesteld.

        We gebruiken hier dezelfde twee helpers als de gewone webshop:
        `_get_contextual_price` voor de prijslijst, en `_apply_taxes_to_price`
        om btw wel of niet mee te tellen naargelang de website-instelling
        "Prijzen tonen incl./excl. btw". Zo staat op het kaartje hetzelfde
        soort bedrag als in de rest van je shop.
        """
        self.ensure_one()
        product = self.product_id
        if pricelist:
            product = product.with_context(pricelist=pricelist.id)
        price = product._get_contextual_price()

        website = self.env['website'].get_current_website()
        if not website:
            return price
        taxes = product.taxes_id.filtered(lambda t: t.company_id == self.env.company)
        return self.env['product.template']._apply_taxes_to_price(
            price,
            pricelist.currency_id if pricelist else product.currency_id,
            taxes,
            taxes,
            product,
            website=website,
        )

    def _get_price_label(self):
        """Korte uitleg waar die stukprijs voor staat, bv. "per persoon/dag"."""
        self.ensure_one()
        if self.qty_mode == 'per_person':
            unit = _("per persoon")
        elif self.qty_mode == 'per_x':
            unit = _("per %(count)s personen", count=int(self.qty_factor or 0))
        else:
            unit = _("per stuk")
        if self.charge_per_day:
            unit = _("%(unit)s, per dag", unit=unit)
        return unit

    def _get_image_url(self):
        """URL naar de afbeelding, met terugval op de productafbeelding.

        `/web/image/<model>/<id>/<veld>/<breedte>x<hoogte>` is de standaard
        Odoo-route die een afbeeldingsveld uitserveert. De bezoeker moet
        leesrechten hebben op het record; die geven we in ir.model.access.csv.
        """
        self.ensure_one()
        if self.image:
            return f'/web/image/event.builder.option/{self.id}/image/400x300'
        if self.product_id.image_512:
            return f'/web/image/product.product/{self.product_id.id}/image_512/400x300'
        return '/web/static/img/placeholder.png'
