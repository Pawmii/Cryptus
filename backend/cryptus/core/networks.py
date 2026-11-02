"""Static registry of networks and built-in assets.

Assets are always identified by a canonical, network-scoped identifier — never
by ticker alone:

    <network>:native
    <network>:erc20:<checksummed contract address>
    <network>:jetton:<raw jetton master address, e.g. 0:b113…>

Testnet coins borrow their mainnet counterpart's price source so fiat-priced test-mode invoices can be
quoted; the coins themselves remain valueless.

Tickers are display metadata only. Two assets may share a ticker (for example
the native GRAM coin of TON and unrelated third-party tokens that also call
themselves "GRAM"); they never share a canonical id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Family = Literal["bitcoin", "evm", "ton", "litecoin", "dogecoin", "tron", "solana"]
Environment = Literal["mainnet", "testnet"]
ImplStatus = Literal["implemented", "planned"]


@dataclass(frozen=True)
class AssetDef:
    network: str
    kind: Literal["native", "erc20", "jetton"]
    symbol: str
    name: str
    decimals: int
    contract: str | None = None  # erc20: checksummed address; jetton: raw master address
    coingecko_id: str | None = None

    @property
    def id(self) -> str:
        if self.kind == "native":
            return f"{self.network}:native"
        return f"{self.network}:{self.kind}:{self.contract}"


@dataclass(frozen=True)
class NetworkDef:
    key: str
    family: Family
    name: str
    environment: Environment
    status: ImplStatus
    native_symbol: str
    native_name: str
    native_decimals: int
    native_coingecko: str | None
    default_confirmations: int
    block_time: float
    explorer_tx: str = ""
    explorer_address: str = ""
    chain_id: int | None = None
    btc_network: Literal["main", "test", "signet", "regtest"] | None = None
    ton_testnet: bool = False
    tokens: tuple[AssetDef, ...] = field(default_factory=tuple)

    @property
    def native(self) -> AssetDef:
        return AssetDef(self.key, "native", self.native_symbol, self.native_name,
                        self.native_decimals, None, self.native_coingecko)

    @property
    def assets(self) -> tuple[AssetDef, ...]:
        return (self.native, *self.tokens)


def _erc20(net: str, symbol: str, name: str, decimals: int, contract: str, cg: str) -> AssetDef:
    return AssetDef(net, "erc20", symbol, name, decimals, contract, cg)


# Raw form of the official USDT jetton master on TON mainnet
# (user-friendly: EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs).
TON_USDT_MASTER_RAW = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"

_NETWORKS: list[NetworkDef] = [
    # ── Bitcoin ──────────────────────────────────────────────────────────────
    NetworkDef("btc", "bitcoin", "Bitcoin", "mainnet", "implemented", "BTC", "Bitcoin", 8, "bitcoin",
               default_confirmations=2, block_time=600,
               explorer_tx="https://mempool.space/tx/{txid}",
               explorer_address="https://mempool.space/address/{address}", btc_network="main"),
    NetworkDef("btc-testnet", "bitcoin", "Bitcoin Testnet", "testnet", "implemented", "tBTC", "Test Bitcoin", 8, "bitcoin",
               default_confirmations=1, block_time=600,
               explorer_tx="https://mempool.space/testnet4/tx/{txid}",
               explorer_address="https://mempool.space/testnet4/address/{address}", btc_network="test"),
    # ── TON ──────────────────────────────────────────────────────────────────
    NetworkDef("ton", "ton", "TON", "mainnet", "implemented", "GRAM", "GRAM", 9, "the-open-network",
               default_confirmations=1, block_time=5,
               explorer_tx="https://tonviewer.com/transaction/{txid}",
               explorer_address="https://tonviewer.com/{address}",
               tokens=(AssetDef("ton", "jetton", "USDT", "Tether USD (Jetton)", 6, TON_USDT_MASTER_RAW, "tether"),)),
    NetworkDef("ton-testnet", "ton", "TON Testnet", "testnet", "implemented", "tGRAM", "Test GRAM", 9, "the-open-network",
               default_confirmations=1, block_time=5,
               explorer_tx="https://testnet.tonviewer.com/transaction/{txid}",
               explorer_address="https://testnet.tonviewer.com/{address}", ton_testnet=True),
    # ── EVM ──────────────────────────────────────────────────────────────────
    NetworkDef("eth", "evm", "Ethereum", "mainnet", "implemented", "ETH", "Ether", 18, "ethereum",
               default_confirmations=12, block_time=12, chain_id=1,
               explorer_tx="https://etherscan.io/tx/{txid}", explorer_address="https://etherscan.io/address/{address}",
               tokens=(
                   _erc20("eth", "USDT", "Tether USD", 6, "0xdAC17F958D2ee523a2206206994597C13D831ec7", "tether"),
                   _erc20("eth", "USDC", "USD Coin", 6, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "usd-coin"),
               )),
    NetworkDef("bsc", "evm", "BNB Smart Chain", "mainnet", "implemented", "BNB", "BNB", 18, "binancecoin",
               default_confirmations=15, block_time=0.75, chain_id=56,
               explorer_tx="https://bscscan.com/tx/{txid}", explorer_address="https://bscscan.com/address/{address}",
               tokens=(
                   _erc20("bsc", "USDT", "Tether USD (BEP-20)", 18, "0x55d398326f99059fF775485246999027B3197955", "tether"),
                   _erc20("bsc", "USDC", "USD Coin (BEP-20)", 18, "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "usd-coin"),
               )),
    NetworkDef("polygon", "evm", "Polygon PoS", "mainnet", "implemented", "POL", "POL", 18, "polygon-ecosystem-token",
               default_confirmations=64, block_time=2, chain_id=137,
               explorer_tx="https://polygonscan.com/tx/{txid}", explorer_address="https://polygonscan.com/address/{address}",
               tokens=(
                   _erc20("polygon", "USDT", "Tether USD (PoS)", 6, "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", "tether"),
                   _erc20("polygon", "USDC", "USD Coin", 6, "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "usd-coin"),
               )),
    NetworkDef("arbitrum", "evm", "Arbitrum One", "mainnet", "implemented", "ETH", "Ether", 18, "ethereum",
               default_confirmations=20, block_time=0.25, chain_id=42161,
               explorer_tx="https://arbiscan.io/tx/{txid}", explorer_address="https://arbiscan.io/address/{address}",
               tokens=(
                   _erc20("arbitrum", "USDT", "Tether USD", 6, "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "tether"),
                   _erc20("arbitrum", "USDC", "USD Coin", 6, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "usd-coin"),
               )),
    NetworkDef("base", "evm", "Base", "mainnet", "implemented", "ETH", "Ether", 18, "ethereum",
               default_confirmations=20, block_time=2, chain_id=8453,
               explorer_tx="https://basescan.org/tx/{txid}", explorer_address="https://basescan.org/address/{address}",
               tokens=(
                   _erc20("base", "USDC", "USD Coin", 6, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "usd-coin"),
               )),
    NetworkDef("eth-sepolia", "evm", "Ethereum Sepolia", "testnet", "implemented", "SepoliaETH", "Sepolia Ether", 18, "ethereum",
               default_confirmations=3, block_time=12, chain_id=11155111,
               explorer_tx="https://sepolia.etherscan.io/tx/{txid}",
               explorer_address="https://sepolia.etherscan.io/address/{address}"),
    NetworkDef("bsc-testnet", "evm", "BNB Smart Chain Testnet", "testnet", "implemented", "tBNB", "Test BNB", 18, "binancecoin",
               default_confirmations=3, block_time=0.75, chain_id=97,
               explorer_tx="https://testnet.bscscan.com/tx/{txid}",
               explorer_address="https://testnet.bscscan.com/address/{address}"),
    NetworkDef("polygon-amoy", "evm", "Polygon Amoy", "testnet", "implemented", "POL", "Test POL", 18, "polygon-ecosystem-token",
               default_confirmations=3, block_time=2, chain_id=80002,
               explorer_tx="https://amoy.polygonscan.com/tx/{txid}",
               explorer_address="https://amoy.polygonscan.com/address/{address}"),
    NetworkDef("arbitrum-sepolia", "evm", "Arbitrum Sepolia", "testnet", "implemented", "ETH", "Sepolia Ether", 18, "ethereum",
               default_confirmations=3, block_time=0.25, chain_id=421614,
               explorer_tx="https://sepolia.arbiscan.io/tx/{txid}",
               explorer_address="https://sepolia.arbiscan.io/address/{address}"),
    NetworkDef("base-sepolia", "evm", "Base Sepolia", "testnet", "implemented", "ETH", "Sepolia Ether", 18, "ethereum",
               default_confirmations=3, block_time=2, chain_id=84532,
               explorer_tx="https://sepolia.basescan.org/tx/{txid}",
               explorer_address="https://sepolia.basescan.org/address/{address}"),
    # ── Planned (adapters reserved; never shown as operational) ─────────────
    NetworkDef("ltc", "litecoin", "Litecoin", "mainnet", "planned", "LTC", "Litecoin", 8, "litecoin", 6, 150),
    NetworkDef("doge", "dogecoin", "Dogecoin", "mainnet", "planned", "DOGE", "Dogecoin", 8, "dogecoin", 6, 60),
    NetworkDef("tron", "tron", "TRON", "mainnet", "planned", "TRX", "TRX", 6, "tron", 20, 3),
    NetworkDef("solana", "solana", "Solana", "mainnet", "planned", "SOL", "SOL", 9, "solana", 32, 0.4),
]

NETWORKS: dict[str, NetworkDef] = {n.key: n for n in _NETWORKS}


def get_network(key: str) -> NetworkDef:
    try:
        return NETWORKS[key]
    except KeyError:
        raise KeyError(f"unknown network {key!r}") from None


def builtin_assets() -> list[AssetDef]:
    return [a for n in _NETWORKS if n.status == "implemented" for a in n.assets]


def parse_asset_id(asset_id: str) -> tuple[str, str, str | None]:
    parts = asset_id.split(":", 2)
    if len(parts) == 2 and parts[1] == "native":
        return parts[0], "native", None
    if len(parts) == 3 and parts[1] in ("erc20", "jetton"):
        return parts[0], parts[1], parts[2]
    raise ValueError(f"malformed asset id {asset_id!r}")


def keychain_family(network: NetworkDef) -> str:
    """Networks that share a key-derivation scheme share a keychain family."""
    return network.family
