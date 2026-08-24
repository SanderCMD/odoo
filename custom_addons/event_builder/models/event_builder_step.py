# Part of the Event Builder module.
from odoo import _, api, fields, models


class EventBuilderStep(models.Model):
    """Eén bouwblok in de configurator, bv. "Catering" of "Zitplaatsen"."""

    _name = 'event.builder.step'
    _description = "Event Builder Step"
    _order = 'sequence, id'

    config_id = fields.Many2one(
        comodel_name='event.builder.config',
        string="Configurator",
        required=True,
        # ondelete='cascade': verwijder je de configurator, dan verdwijnen ook
        # al zijn stappen. Zonder dit zou Odoo het verwijderen blokkeren.
        ondelete='cascade',
        index=True,
    )

    name = fields.Char(string="Titel", required=True, translate=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(string="Uitleg", translate=True)

    selection_type = fields.Selection(
        selection=[
            ('single', "Eén keuze"),
            ('multi', "Meerdere keuzes"),
        ],
        string="Keuzetype",
        default='single',
        required=True,
    )

    # LET OP de naam: `required` is al een ingebouwd argument van fields.
    # Een veld zo noemen geeft verwarrende conflicten, vandaar `is_required`.
    is_required = fields.Boolean(
        string="Verplicht",
        help="De bezoeker moet hier minstens één optie kiezen.",
    )

    option_ids = fields.One2many(
        comodel_name='event.builder.option',
        inverse_name='step_id',
        string="Opties",
    )

    option_count = fields.Integer(compute='_compute_option_count')

    @api.depends('option_ids')
    def _compute_option_count(self):
        for step in self:
            step.option_count = len(step.option_ids)

    def action_add_missing_variants(self):
        """Vul de stap aan met varianten die nog geen optie hebben.

        Werk je met varianten - bv. één product "Catering" met een attribuut
        Type (Italiaans, Spaans, BBQ, ...) - dan moet elke variant hier een
        eigen optie krijgen. Voeg je later in de catalogus een variant toe,
        dan verschijnt die NIET vanzelf in de configurator.

        Deze knop kijkt welke producttemplates al in deze stap gebruikt worden
        en maakt een optie voor elke variant die nog ontbreekt. De instellingen
        (hoeveelheidslogica, per dag) neemt hij over van een bestaande optie
        van datzelfde product, zodat je enkel nog naam en foto hoeft na te
        kijken.
        """
        self.ensure_one()

        created = self.env['event.builder.option']
        # De templates die al vertegenwoordigd zijn in deze stap.
        templates = self.option_ids.product_id.product_tmpl_id
        existing_product_ids = set(self.option_ids.product_id.ids)
        last_sequence = max(self.option_ids.mapped('sequence') or [0])

        for template in templates:
            # Een bestaande optie van dit product dient als voorbeeld voor de
            # instellingen van de nieuwe.
            template_option = self.option_ids.filtered(
                lambda option: option.product_id.product_tmpl_id == template
            )[:1]

            for variant in template.product_variant_ids:
                if variant.id in existing_product_ids:
                    continue
                last_sequence += 10
                created |= self.env['event.builder.option'].create({
                    'step_id': self.id,
                    'product_id': variant.id,
                    'sequence': last_sequence,
                    'qty_mode': template_option.qty_mode,
                    'qty_factor': template_option.qty_factor,
                    'charge_per_day': template_option.charge_per_day,
                })

        # Een client action met type 'display_notification' toont een melding
        # rechtsboven zonder de pagina te verlaten. `sticky: False` laat ze
        # na enkele seconden vanzelf verdwijnen.
        if created:
            message = _("%(count)s variant(en) toegevoegd.", count=len(created))
        else:
            message = _("Alle varianten staan er al in.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': 'success' if created else 'info',
                'sticky': False,
            },
        }
