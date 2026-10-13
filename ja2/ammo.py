"""Which magazine fits which gun, and how many rounds each holds.

Taken from the JA2 Stracciatella project's externalized item data
(assets/externalized/weapons.json and magazines.json), which mirrors the
tables compiled into the original ja2.exe. Item numbers are the vanilla
ones, the same numbering item_data.ITEMS uses; every one was checked to
exist there when this file was generated.
"""

# ubGunAmmoType values, in the game's own order (AmmoTypes in Weapons.h)
AMMO_TYPES = (
    'Regular',
    'Hollow point',
    'Armour piercing',
    'Super armour piercing',
    'Buckshot',
    'Flechette',
    'Grenade',
    'Monster',
    'Knife',
    'High explosive',
    'HEAT',
    'Sleep dart',
    'Flame',
)

# gun item -> (calibre, magazine size)
GUNS = {
    1: ('AMMO9', 15),    # Glock 17
    2: ('AMMO9', 15),    # Glock 18
    3: ('AMMO9', 15),    # Beretta 92F
    4: ('AMMO9', 15),    # Beretta 92R
    5: ('AMMO38', 6),    # .38 Special
    6: ('AMMO357', 6),    # Barracuda
    7: ('AMMO357', 9),    # Desert Eagle
    8: ('AMMO45', 7),    # Colt .45
    9: ('AMMO9', 30),    # H&K MP5K
    10: ('AMMO45', 30),    # MAC-10
    11: ('AMMO45', 30),    # Thompson M1A1
    12: ('AMMO556', 30),    # Colt Commando
    13: ('AMMO556', 30),    # H&K MP53
    14: ('AMMO545', 30),    # AKSU-74
    15: ('AMMO57', 50),    # FN-P90
    16: ('AMMO762W', 30),    # Type-85
    17: ('AMMO762W', 10),    # SKS
    18: ('AMMO762W', 10),    # Dragunov
    19: ('AMMO762N', 5),    # M24
    20: ('AMMO556', 30),    # Steyr AUG
    21: ('AMMO556', 30),    # H&K G41
    22: ('AMMO556', 30),    # Ruger Mini-14
    23: ('AMMO556', 30),    # C-7
    24: ('AMMO556', 30),    # FA-MAS
    25: ('AMMO545', 30),    # AK-74
    26: ('AMMO762W', 30),    # AKM
    27: ('AMMO762N', 20),    # M14
    28: ('AMMO762N', 20),    # FN-FAL
    29: ('AMMO762N', 20),    # H&K G3A3
    30: ('AMMO47', 50),    # H&K G11
    31: ('AMMO12G', 7),    # Remington M870
    32: ('AMMO12G', 7),    # SPAS-15
    33: ('AMMOCAWS', 10),    # CAWS
    34: ('AMMO556', 30),    # FN Minimi
    35: ('AMMO545', 30),    # RPK-74
    36: ('AMMO762N', 20),    # H&K 21
    48: ('AMMOMONST', 50),    # Queen Creature Spit
    55: ('AMMOROCKET', 5),    # Rocket Rifle
    56: ('AMMO762N', 5),    # Automag III
    57: ('AMMOMONST', 5),    # Infant Creature Spit
    58: ('AMMOMONST', 10),    # Young Male Spit
    59: ('AMMOMONST', 20),    # Old Male Spit
    61: ('AMMODART', 1),    # Dart Gun
    63: ('AMMOFLAME', 5),    # Flamethrower
    65: ('AMMOROCKET', 5),    # Auto Rocket Rifle
}

# magazine item -> (calibre, capacity, ammo type)
MAGAZINES = {
    71: ('AMMO9', 15, 0),    # 9mm Pistol Magazine
    72: ('AMMO9', 30, 0),    # 9mm SMG Magazine
    73: ('AMMO9', 15, 2),    # 9mm Pistol Magazine, AP
    74: ('AMMO9', 30, 2),    # 9mm SMG Magazine, AP
    75: ('AMMO9', 15, 1),    # 9mm Pistol Magazine, HP
    76: ('AMMO9', 30, 1),    # 9mm SMG Magazine, HP
    77: ('AMMO38', 6, 0),    # .38 Speed Loader
    78: ('AMMO38', 6, 2),    # .38 Speed Loader, AP
    79: ('AMMO38', 6, 1),    # .38 Speed Loader, HP
    80: ('AMMO45', 7, 0),    # .45 Pistol Magazine
    81: ('AMMO45', 30, 0),    # .45 SMG Magazine
    82: ('AMMO45', 7, 2),    # .45 Pistol Magazine, AP
    83: ('AMMO45', 30, 2),    # .45 SMG Magazine, AP
    84: ('AMMO45', 7, 1),    # .45 Pistol Magazine, HP
    85: ('AMMO45', 30, 1),    # .45 SMG Magazine, HP
    86: ('AMMO357', 6, 0),    # .357 Speed Loader
    87: ('AMMO357', 9, 0),    # .357 Magazine
    88: ('AMMO357', 6, 2),    # .357 Speed Loader, AP
    89: ('AMMO357', 9, 2),    # .357 Magazine, AP
    90: ('AMMO357', 6, 1),    # .357 Speed Loader, HP
    91: ('AMMO357', 9, 1),    # .357 Magazine, HP
    92: ('AMMO545', 30, 2),    # 5.45mm Magazine
    93: ('AMMO545', 30, 1),    # 5.45mm Magazine, HP
    94: ('AMMO556', 30, 2),    # 5.56mm Magazine
    95: ('AMMO556', 30, 1),    # 5.56mm Magazine, HP
    96: ('AMMO762W', 10, 2),    # 7.62mm WP Magazine, 10
    97: ('AMMO762W', 30, 2),    # 7.62mm WP Magazine, 30
    98: ('AMMO762W', 10, 1),    # 7.62mm WP Magazine, 10 HP
    99: ('AMMO762W', 30, 1),    # 7.62mm WP Magazine, 30 HP
    100: ('AMMO762N', 5, 2),    # 7.62mm NATO Magazine, 5
    101: ('AMMO762N', 20, 2),    # 7.62mm NATO Magazine, 20
    102: ('AMMO762N', 5, 1),    # 7.62mm NATO Ammunition, 5 HP
    103: ('AMMO762N', 20, 1),    # 7.62mm NATO Magazine, 20 HP
    104: ('AMMO47', 50, 3),    # 4.7mm Magazine
    105: ('AMMO57', 50, 2),    # 5.7mm Magazine
    106: ('AMMO57', 50, 1),    # 5.7mm Magazine, HP
    107: ('AMMO12G', 7, 0),    # 12 Gauge Magazine
    108: ('AMMO12G', 7, 4),    # 12 Gauge Magazine, Buckshot
    109: ('AMMOCAWS', 10, 3),    # CAWS Magazine
    110: ('AMMOCAWS', 10, 4),    # CAWS Magazine, Buckshot
    111: ('AMMOROCKET', 5, 3),    # Minirockets, AP
    112: ('AMMOROCKET', 5, 9),    # Minirockets, HP
    113: ('AMMOROCKET', 5, 10),    # Minirockets, HEAP
    114: ('AMMODART', 1, 11),    # Tranquilizer Dart
}


def is_gun(item):
    return item in GUNS


def fits(gun):
    """The magazines that can be loaded into `gun`, in item order."""
    cal = GUNS.get(gun, (None,))[0]
    return [m for m, v in sorted(MAGAZINES.items()) if v[0] == cal]


def capacity(gun):
    return GUNS.get(gun, (None, 0))[1]


def ammo_type(mag):
    return MAGAZINES[mag][2] if mag in MAGAZINES else 0
