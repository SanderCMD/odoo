# Part of the Event Builder module.
from odoo import api, fields, models


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
