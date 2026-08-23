{
    'name': "Event Builder",
    'summary': "One-page event configurator with live pricing for the website",
    'description': """
Laat een bezoeker zijn event samenstellen op één pagina (datums, aantal
personen, catering, meubilair, ...) met een prijs die live meebeweegt, en
zet die keuze om in een offerte.

De keuzemogelijkheden worden volledig beheerd in het ERP
(Verkoop > Configuratie > Event Builder) en verwijzen naar echte producten,
zodat voorraad, inkoop, marge en facturatie normaal blijven werken.
    """,
    'author': "Sander",
    'category': 'Website/Website',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',

    # Modules die geinstalleerd moeten zijn voordat deze module kan werken.
    # Odoo installeert ze automatisch mee.
    'depends': [
        'website_sale',      # webshop, winkelmandje, checkout
        'sale_management',   # offertes met optionele producten
    ],

    # XML/CSV bestanden die bij installatie in de database geladen worden.
    # De volgorde is belangrijk: security eerst, dan views, dan menus.
    'data': [
        'security/ir.model.access.csv',
        'views/event_builder_views.xml',
        'views/event_builder_menus.xml',
        'views/snippets.xml',
    ],

    # Alleen geladen met `--load-language`/demo data aan. Handig voor de PoC.
    'demo': [
        'data/demo_data.xml',
    ],

    # Frontend-assets: wat er meegestuurd wordt naar de browser van de bezoeker.
    # 'web.assets_frontend' is de bundel voor de publieke website
    # (niet de backend). Alles hierin wordt samengevoegd en geminified.
    'assets': {
        'web.assets_frontend': [
            'event_builder/static/src/scss/event_builder.scss',
            'event_builder/static/src/js/builder/**/*',
            'event_builder/static/src/snippets/s_event_builder/*.js',
        ],
    },

    'installable': True,
    'application': True,
}
