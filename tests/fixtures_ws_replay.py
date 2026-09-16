"""Offline WebSocket fixtures for observation-monitor replay.

Recorded PumpPortal WS events for testing ObservationMonitor without
live connection. Events are sanitized (no real wallet addresses).
"""

from __future__ import annotations

import time
from typing import Any

# Sample create event (new token launch)
SAMPLE_CREATE_EVENT: dict[str, Any] = {
    "txType": "create",
    "mint": "TestMint111111111111111111111111111111",
    "name": "Test Token",
    "symbol": "TEST",
    "description": "A test token for offline replay",
    "image": "https://example.com/image.png",
    "metadata": "https://example.com/metadata.json",
    "traderPublicKey": "CreatorWallet11111111111111111111111111",
    "timestamp": time.time() * 1000,
    "vSolInBondingCurve": 30.0,
    "marketCapSol": 30.0,
    "vTokensInBondingCurve": 715_333_460_666_667,
}

# Sample buy event
SAMPLE_BUY_EVENT: dict[str, Any] = {
    "txType": "buy",
    "mint": "TestMint111111111111111111111111111111",
    "traderPublicKey": "BuyerWallet11111111111111111111111111111",
    "solAmount": 0.1,
    "tokenAmount": 1_000_000,
    "timestamp": time.time() * 1000 + 1000,
    "vSolInBondingCurve": 30.1,
    "marketCapSol": 30.1,
}

# Sample sell event
SAMPLE_SELL_EVENT: dict[str, Any] = {
    "txType": "sell",
    "mint": "TestMint111111111111111111111111111111",
    "traderPublicKey": "SellerWallet1111111111111111111111111111",
    "solAmount": 0.05,
    "tokenAmount": 500_000,
    "timestamp": time.time() * 1000 + 2000,
    "vSolInBondingCurve": 30.05,
    "marketCapSol": 30.05,
}

# Sample migration/graduation event
SAMPLE_MIGRATION_EVENT: dict[str, Any] = {
    "txType": "buy",
    "mint": "TestMint111111111111111111111111111111",
    "traderPublicKey": "BuyerWallet22222222222222222222222222222",
    "solAmount": 50.0,
    "tokenAmount": 50_000_000,
    "timestamp": time.time() * 1000 + 10000,
    "vSolInBondingCurve": 85.0,
    "marketCapSol": 85.0,
    "complete": True,
    "raydium_pool": "RaydiumPool111111111111111111111111111111",
}


def replay_sequence() -> list[dict[str, Any]]:
    """Return a sequence of WS events for replay testing.
    
    Simulates a token launch -> trades -> graduation lifecycle.
    """
    now_ms = time.time() * 1000
    mint = "ReplayMint1111111111111111111111111111111"
    creator = "ReplayCreator111111111111111111111111111"
    
    events = []
    
    # Create
    events.append({
        "txType": "create",
        "mint": mint,
        "name": "Replay Token",
        "symbol": "RPL",
        "description": "Token for offline replay testing",
        "image": "https://example.com/rpl.png",
        "traderPublicKey": creator,
        "timestamp": now_ms,
        "vSolInBondingCurve": 30.0,
        "marketCapSol": 30.0,
    })
    
    # Initial buys from different wallets
    for i in range(1, 6):
        events.append({
            "txType": "buy",
            "mint": mint,
            "traderPublicKey": f"Buyer{i}111111111111111111111111111111111",
            "solAmount": 0.1 * i,
            "tokenAmount": 1_000_000 * i,
            "timestamp": now_ms + (i * 1000),
            "vSolInBondingCurve": 30.0 + (0.1 * i),
            "marketCapSol": 30.0 + (0.1 * i),
        })
    
    # A sell
    events.append({
        "txType": "sell",
        "mint": mint,
        "traderPublicKey": "Seller1111111111111111111111111111111111",
        "solAmount": 0.2,
        "tokenAmount": 2_000_000,
        "timestamp": now_ms + 6000,
        "vSolInBondingCurve": 30.3,
        "marketCapSol": 30.3,
    })
    
    # More buys
    for i in range(6, 10):
        events.append({
            "txType": "buy",
            "mint": mint,
            "traderPublicKey": f"Buyer{i}111111111111111111111111111111111",
            "solAmount": 0.15,
            "tokenAmount": 1_500_000,
            "timestamp": now_ms + (i * 1000),
            "vSolInBondingCurve": 30.0 + (0.15 * (i - 5)),
            "marketCapSol": 30.0 + (0.15 * (i - 5)),
        })
    
    return events


def multiple_launches_sequence() -> list[dict[str, Any]]:
    """Return multiple concurrent launch events for stress testing."""
    now_ms = time.time() * 1000
    events = []
    
    for launch_idx in range(3):
        mint = f"Launch{launch_idx}Mint111111111111111111111111"
        creator = f"Launch{launch_idx}Creator111111111111111111"
        
        # Create
        events.append({
            "txType": "create",
            "mint": mint,
            "name": f"Launch {launch_idx}",
            "symbol": f"L{launch_idx}",
            "traderPublicKey": creator,
            "timestamp": now_ms + (launch_idx * 100),
            "vSolInBondingCurve": 30.0,
            "marketCapSol": 30.0,
        })
        
        # A few buys per launch
        for i in range(3):
            events.append({
                "txType": "buy",
                "mint": mint,
                "traderPublicKey": f"L{launch_idx}Buyer{i}11111111111111111111",
                "solAmount": 0.1,
                "tokenAmount": 1_000_000,
                "timestamp": now_ms + (launch_idx * 100) + (i + 1) * 500,
                "vSolInBondingCurve": 30.0 + (0.1 * (i + 1)),
                "marketCapSol": 30.0 + (0.1 * (i + 1)),
            })
    
    return events


__all__ = [
    "SAMPLE_CREATE_EVENT",
    "SAMPLE_BUY_EVENT",
    "SAMPLE_SELL_EVENT",
    "SAMPLE_MIGRATION_EVENT",
    "replay_sequence",
    "multiple_launches_sequence",
]
