"""
DN42 BGP Community Constants

Reference: https://dn42.eu/howto/Bird-communities
"""
from typing import Optional, Tuple

# DN42 Community Prefix
DN42_COMMUNITY_ASN = 64511

# Latency Communities (64511, 1-9) - Round Trip Time
DN42_LATENCY = {
    0: (64511, 1),   # RTT < 2.7ms
    1: (64511, 2),   # RTT < 7.3ms
    2: (64511, 3),   # RTT < 20ms
    3: (64511, 4),   # RTT < 55ms
    4: (64511, 5),   # RTT < 148ms
    5: (64511, 6),   # RTT < 403ms
    6: (64511, 7),   # RTT < 1097ms
    7: (64511, 8),   # RTT < 2981ms
    8: (64511, 9),   # RTT >= 2981ms
}

# RTT thresholds in milliseconds (upper bound for each tier)
LATENCY_THRESHOLDS = [2.7, 7.3, 20, 55, 148, 403, 1097, 2981]

# Bandwidth Communities (64511, 21-25)
DN42_BANDWIDTH = {
    "100m": (64511, 21),   # >= 100 Mbps
    "10g":  (64511, 22),   # >= 10 Gbps
    "1g":   (64511, 23),   # >= 1 Gbps
    "100k": (64511, 24),   # >= 100 Kbps
    "10m":  (64511, 25),   # >= 10 Mbps
}

# Crypto Communities (64511, 31-34)
DN42_CRYPTO = {
    "none":      (64511, 31),  # No encryption
    "unsafe":    (64511, 32),  # Encrypted but insecure
    "encrypted": (64511, 33),  # Encrypted (WireGuard, OpenVPN)
    "latency":   (64511, 34),  # Encrypted with latency-critical setup
}

# Region Communities (64511, 41-53)
DN42_REGION = {
    "eu":    (64511, 41),   # Europe
    "na-e":  (64511, 42),   # North America - East
    "na-c":  (64511, 43),   # North America - Central
    "na-w":  (64511, 44),   # North America - West
    "ca":    (64511, 45),   # Central America
    "sa":    (64511, 46),   # South America
    "af":    (64511, 47),   # Africa
    "as-s":  (64511, 48),   # Asia - South (India, etc.)
    "as-se": (64511, 49),   # Asia - Southeast
    "as-e":  (64511, 50),   # Asia - East (China, Japan, Korea)
    "oc":    (64511, 51),   # Oceania (AU, NZ)
    "me":    (64511, 52),   # Middle East
    "as-n":  (64511, 53),   # Asia - North (Russia/Siberia)
}

# Action Communities
DN42_ACTIONS = {
    "no_export":   (64511, 65281),  # Do not export to any peer
    "no_announce": (64511, 65282),  # Do not announce (local only)
}

# =============================================================================
# MoeNet Large Communities (4242420998, type, value)
# For internal cold potato routing
# =============================================================================

MOENET_ASN = 4242420998

# Type 1: Continent Origin
MOENET_CONTINENT = {
    "AS": (MOENET_ASN, 1, 100),   # Asia
    "NA": (MOENET_ASN, 1, 200),   # North America
    "EU": (MOENET_ASN, 1, 300),   # Europe
    "OC": (MOENET_ASN, 1, 400),   # Oceania
}

# Type 2: Sub-region (more granular)
MOENET_SUBREGION = {
    # Asia
    "as-e":  (MOENET_ASN, 2, 101),   # East: HK, JP, KR, TW
    "as-se": (MOENET_ASN, 2, 102),   # Southeast: SG, MY
    "as-s":  (MOENET_ASN, 2, 103),   # South: IN
    "as-n":  (MOENET_ASN, 2, 104),   # North: RU
    # North America
    "na-e":  (MOENET_ASN, 2, 201),   # East coast
    "na-c":  (MOENET_ASN, 2, 202),   # Central
    "na-w":  (MOENET_ASN, 2, 203),   # West coast
    # Europe (MoeNet extension)
    "eu-w":  (MOENET_ASN, 2, 301),   # Western: GB
    "eu-c":  (MOENET_ASN, 2, 302),   # Central: DE, CH
    # Oceania
    "oc":    (MOENET_ASN, 2, 401),   # AU, NZ
}

# Type 4: Link Characteristics
MOENET_LINK = {
    "intercontinental": (MOENET_ASN, 4, 1),
    "high_latency":     (MOENET_ASN, 4, 2),
    "low_mtu":          (MOENET_ASN, 4, 3),
}

# All communities for reference
ALL_COMMUNITIES = {
    "latency": DN42_LATENCY,
    "bandwidth": DN42_BANDWIDTH,
    "crypto": DN42_CRYPTO,
    "region": DN42_REGION,
    "actions": DN42_ACTIONS,
    "moenet_continent": MOENET_CONTINENT,
    "moenet_subregion": MOENET_SUBREGION,
    "moenet_link": MOENET_LINK,
}


def latency_to_tier(rtt_ms: float) -> int:
    """Convert RTT in milliseconds to latency tier (0-8)."""
    for tier, threshold in enumerate(LATENCY_THRESHOLDS):
        if rtt_ms < threshold:
            return tier
    return 8  # Highest tier (slowest)


def tier_to_latency_range(tier: int) -> Tuple[float, float]:
    """Get RTT range for a tier. Returns (min_ms, max_ms)."""
    if tier < 0 or tier > 8:
        tier = 8
    
    if tier == 0:
        return (0, 2.7)
    elif tier == 8:
        return (2981, float('inf'))
    else:
        return (LATENCY_THRESHOLDS[tier - 1], LATENCY_THRESHOLDS[tier])


def region_name_to_code(name: str) -> Optional[str]:
    """Convert region name to code (e.g., 'Hong Kong' -> 'as-e')."""
    name_lower = name.lower().strip()
    
    # Direct mappings
    mappings = {
        # Asia - East
        "hk": "as-e", "hongkong": "as-e", "hong kong": "as-e",
        "jp": "as-e", "japan": "as-e", "tokyo": "as-e", "osaka": "as-e",
        "kr": "as-e", "korea": "as-e", "seoul": "as-e",
        "cn": "as-e", "china": "as-e", "shanghai": "as-e", "beijing": "as-e",
        "tw": "as-e", "taiwan": "as-e", "taipei": "as-e",
        # Asia - Southeast
        "sg": "as-se", "singapore": "as-se",
        "my": "as-se", "malaysia": "as-se", "kuala lumpur": "as-se",
        "th": "as-se", "thailand": "as-se", "bangkok": "as-se",
        "vn": "as-se", "vietnam": "as-se", "hanoi": "as-se", "ho chi minh": "as-se",
        "id": "as-se", "indonesia": "as-se", "jakarta": "as-se",
        "ph": "as-se", "philippines": "as-se", "manila": "as-se",
        # Asia - South
        "in": "as-s", "india": "as-s", "mumbai": "as-s", "delhi": "as-s",
        "bd": "as-s", "bangladesh": "as-s", "dhaka": "as-s",
        "pk": "as-s", "pakistan": "as-s", "karachi": "as-s",
        # Europe
        "de": "eu", "germany": "eu", "frankfurt": "eu", "berlin": "eu",
        "nl": "eu", "netherlands": "eu", "amsterdam": "eu",
        "gb": "eu", "uk": "eu", "london": "eu",
        "fr": "eu", "france": "eu", "paris": "eu",
        # North America - East
        "us-e": "na-e", "new york": "na-e", "nyc": "na-e", "miami": "na-e",
        "washington": "na-e", "dc": "na-e", "boston": "na-e",
        # North America - Central
        "us-c": "na-c", "chicago": "na-c", "dallas": "na-c", "denver": "na-c",
        # North America - West
        "us-w": "na-w", "los angeles": "na-w", "la": "na-w", 
        "san francisco": "na-w", "sf": "na-w", "seattle": "na-w",
        # Oceania
        "au": "oc", "australia": "oc", "sydney": "oc", "melbourne": "oc",
        "nz": "oc", "new zealand": "oc", "auckland": "oc",
    }
    
    return mappings.get(name_lower, None)


def parse_community(community_str: str) -> Tuple[int, int]:
    """Parse community string like '(64511, 1)' to tuple."""
    cleaned = community_str.strip("() ")
    parts = cleaned.split(",")
    if len(parts) == 2:
        return (int(parts[0].strip()), int(parts[1].strip()))
    raise ValueError(f"Invalid community format: {community_str}")


def community_to_str(community: Tuple[int, int]) -> str:
    """Convert community tuple to string."""
    return f"({community[0]}, {community[1]})"


def describe_community(community: Tuple[int, int]) -> str:
    """Get human-readable description of a community."""
    asn, value = community
    
    if asn != DN42_COMMUNITY_ASN:
        return f"Unknown ({asn}, {value})"
    
    # Check latency
    for tier, com in DN42_LATENCY.items():
        if com[1] == value:
            min_rtt, max_rtt = tier_to_latency_range(tier)
            if tier == 8:
                return f"Latency ≥{min_rtt}ms"
            return f"Latency <{max_rtt}ms"
    
    # Check bandwidth
    for name, com in DN42_BANDWIDTH.items():
        if com[1] == value:
            return f"Bandwidth ≥{name.upper()}"
    
    # Check crypto
    for name, com in DN42_CRYPTO.items():
        if com[1] == value:
            return f"Crypto: {name.title()}"
    
    # Check region
    for name, com in DN42_REGION.items():
        if com[1] == value:
            return f"Region: {name.upper()}"
    
    # Check actions
    for name, com in DN42_ACTIONS.items():
        if com[1] == value:
            return f"Action: {name.replace('_', ' ').title()}"
    
    return f"Unknown ({asn}, {value})"
