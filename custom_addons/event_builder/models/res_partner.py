# Part of the Event Builder module.
from odoo import api, fields, models


class ResPartner(models.Model):
    """Kleine uitbreiding op het contactmodel voor de adreskiezer.

    Hier staat alleen wat een adres *is* en hoe het eruitziet op een regel.
    Wat een adres *verplicht* moet bevatten, weet Odoo zelf: dat vragen we in
    de controller op via `_get_mandatory_*_address_fields`, zodat onze regels
    nooit afwijken van de rest van de webshop.
    """

    _inherit = 'res.partner'

    # De velden die de adreskiezer kan tonen en bewaren. Als tuple bovenaan,
    # zodat de controller dezelfde lijst gebruikt bij het uitlezen én bij het
    # opslaan - en er dus nooit een veld kan binnensluipen dat de bezoeker
    # niet had mogen wijzigen.
    EB_ADDRESS_FIELDS = (
        'name', 'street', 'street2', 'zip', 'city',
        'state_id', 'country_id', 'phone', 'email', 'vat',
    )

    def _eb_address_data(self):
        """Het adres als dict voor de configurator, inclusief een regel tekst.

        :return: velden plus `display` (adres op één regel) en `label`
        :rtype: dict
        """
        self.ensure_one()
        data = {'id': self.id}
        for fname in self.EB_ADDRESS_FIELDS:
            field = self._fields[fname]
            value = self[fname]
            if field.type == 'many2one':
                data[fname] = value.id or False
                data[f'{fname}_name'] = value.display_name or ''
            else:
                data[fname] = value or ''
        data['display'] = self._eb_address_line()
        data['label'] = self.name or self.display_name
        return data

    def _eb_address_line(self):
        """Het adres op één regel, voor de samengevouwen weergave.

        `_display_address` geeft een blok van meerdere regels terug, in de
        volgorde die bij het land hoort (in Japan staat de postcode elders dan
        in België). We plakken die regels aan elkaar in plaats van zelf een
        volgorde te verzinnen.
        """
        self.ensure_one()
        parts = [
            line.strip()
            for line in (self._display_address(without_company=True) or '').splitlines()
            if line.strip()
        ]
        return ', '.join(parts)

    @api.model
    def _eb_selectable_addresses(self, partner):
        """De adressen waaruit een ingelogde klant mag kiezen.

        Dat zijn zijn eigen contact en de adressen die eronder hangen. We
        vertrekken van de commerciële entiteit, zodat een medewerker van een
        bedrijf ook de bedrijfsadressen ziet - net zoals in de webshop.
        """
        commercial = partner.commercial_partner_id or partner
        addresses = commercial | commercial.child_ids.filtered(
            lambda p: p.type in ('contact', 'invoice', 'delivery', 'other')
        )
        # De klant zelf altijd eerst, daarna op naam.
        return partner | (addresses - partner).sorted('display_name')
