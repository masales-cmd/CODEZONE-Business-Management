from __future__ import annotations

INDUSTRY_PROFILES = {
    'retail': {
        'label': 'Retail Shop',
        'description': 'General retail for shops, supermarkets and everyday goods.',
        'features': {'inventory', 'customers', 'purchasing', 'finance', 'reports'},
    },
    'wholesale': {
        'label': 'Wholesale & Distribution',
        'description': 'Wholesale sales, customer credit, pricing and fulfillment.',
        'features': {'inventory', 'customers', 'purchasing', 'credit', 'finance', 'reports'},
    },
    'restaurant': {
        'label': 'Restaurant / Cafe',
        'description': 'Food, drinks and service sales with inventory control.',
        'features': {'inventory', 'customers', 'purchasing', 'services', 'finance', 'reports'},
    },
    'pharmacy': {
        'label': 'Pharmacy / Health Shop',
        'description': 'Product sales with stock, supplier and customer controls.',
        'features': {'inventory', 'customers', 'purchasing', 'credit', 'finance', 'reports'},
    },
    'electronics': {
        'label': 'Electronics & Appliances',
        'description': 'Retail and service workflows for electronics and devices.',
        'features': {'inventory', 'customers', 'purchasing', 'services', 'finance', 'reports'},
    },
    'hardware': {
        'label': 'Hardware & Building Supplies',
        'description': 'Bulk goods, units, stock and customer credit.',
        'features': {'inventory', 'customers', 'purchasing', 'credit', 'finance', 'reports'},
    },
    'services': {
        'label': 'Services',
        'description': 'Businesses that sell services as well as optional products.',
        'features': {'inventory', 'customers', 'services', 'finance', 'reports'},
    },
    'manufacturing': {
        'label': 'Manufacturing',
        'description': 'Production-oriented inventory and purchasing foundation.',
        'features': {'inventory', 'customers', 'purchasing', 'finance', 'reports'},
    },
    'beverage': {
        'label': 'Beverage / Drinks',
        'description': 'Beverage operations with returnable containers and distribution.',
        'features': {'inventory', 'customers', 'purchasing', 'credit', 'containers', 'finance', 'reports'},
    },
    'other': {
        'label': 'Other Business',
        'description': 'A flexible general-business profile.',
        'features': {'inventory', 'customers', 'purchasing', 'finance', 'reports'},
    },
}

DEFAULT_INDUSTRY = 'retail'


def normalize_industry(value: str | None) -> str:
    key = (value or DEFAULT_INDUSTRY).strip().lower()
    return key if key in INDUSTRY_PROFILES else DEFAULT_INDUSTRY


def feature_enabled(industry: str | None, feature: str) -> bool:
    return feature in INDUSTRY_PROFILES[normalize_industry(industry)]['features']
