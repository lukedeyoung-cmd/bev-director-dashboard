"""Shared Toast payload fixtures, shaped like the real API (probed 2026-10)."""


def selection(
    guid,
    item_guid,
    name,
    quantity,
    price,
    pre_discount=None,
    *,
    voided=False,
    modifiers=None,
    discounts=None,
):
    return {
        "guid": guid,
        "entityType": "MenuItemSelection",
        "displayName": name,
        "quantity": quantity,
        "price": price,
        "preDiscountPrice": pre_discount if pre_discount is not None else price,
        "voided": voided,
        "voidReason": "Mistake" if voided else None,
        "item": {"guid": item_guid, "entityType": "MenuItem"},
        "modifiers": modifiers or [],
        "appliedDiscounts": discounts or [],
    }


def applied_discount(amount, *, name=None):
    discount = {
        "guid": f"disc-{amount}",
        "entityType": "AppliedExternallyPricedDiscount",
        "discountAmount": amount,
        "nonTaxDiscountAmount": amount,
    }
    if name:
        discount["name"] = name
    return discount


def order(order_guid, checks, *, voided=False, business_date=20261029):
    return {
        "guid": order_guid,
        "entityType": "Order",
        "businessDate": business_date,
        "voided": voided,
        "deleted": False,
        "checks": checks,
    }


def check(check_guid, selections, *, voided=False, discounts=None, amount=None):
    return {
        "guid": check_guid,
        "entityType": "Check",
        "voided": voided,
        "deleted": False,
        "selections": selections,
        "appliedDiscounts": discounts or [],
        "amount": amount,
    }


MENUS_PAYLOAD = {
    "restaurantGuid": "restaurant-guid",
    "restaurantTimeZone": "America/Chicago",
    "menus": [
        {
            "guid": "menu-1",
            "name": "Dinner",
            "menuGroups": [
                {
                    "guid": "group-wine",
                    "name": "Wine",
                    "menuItems": [
                        {
                            "guid": "item-btg",
                            "name": "House White Wine",
                            "price": 14.0,
                            "visibility": ["POS"],
                        },
                        {
                            "guid": "item-bottle",
                            "name": "Paysan Rose BTL",
                            "price": 72.0,
                            "visibility": ["POS"],
                        },
                    ],
                    "menuGroups": [
                        {
                            "guid": "group-btg",
                            "name": "BTG",
                            "menuItems": [
                                {
                                    "guid": "item-btg2",
                                    "name": "House Red BTG",
                                    "price": 13.0,
                                }
                            ],
                        }
                    ],
                },
                {
                    "guid": "group-beer",
                    "name": "Beer",
                    "menuItems": [
                        {"guid": "item-beer", "name": "La Chouffe", "price": 10.0}
                    ],
                },
            ],
        }
    ],
}

CONFIG_ITEMS = [
    {"guid": "item-config-only", "name": "Burger", "entityType": "MenuItem", "type": "NONE"},
    # Same guid as a menus item: must not overwrite the menus classification.
    {"guid": "item-beer", "name": "La Chouffe POS NAME", "entityType": "MenuItem"},
    {"guid": "item-mod", "name": "Extra Shot", "entityType": "MenuItem"},
]
