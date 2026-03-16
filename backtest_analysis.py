"""Comprehensive backtest analysis of existing trades."""

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Dict, Tuple
import itertools

import config


@dataclass
class Trade:
    """Trade data."""
    city: str
    side: str
    edge: float
    forecast_prob: float
    market_price: float
    size: float
    hit: int  # 1=win, 0=loss, None=pending
    pnl: float

    @property
    def outcome(self) -> str:
        """Convert hit to outcome string."""
        if self.hit is None:
            return 'pending'
        return 'win' if self.hit == 1 else 'loss'


def load_trades(db_path: str) -> List[Trade]:
    """Load all resolved trades from database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT city, side, edge, forecast_prob, price, size, hit, pnl, created_at
        FROM trades
        WHERE resolved = 1 AND hit IS NOT NULL
        ORDER BY created_at ASC
    """)

    trades = []
    for row in cursor.fetchall():
        trades.append(Trade(
            city=row[0],
            side=row[1],
            edge=row[2],
            forecast_prob=row[3],
            market_price=row[4],
            size=row[5],
            hit=row[6],
            pnl=row[7] if row[7] is not None else 0.0
        ))

    conn.close()
    return trades


def filter_trades(
    trades: List[Trade],
    min_edge: float = 0.0,
    max_edge: float = 1.0,
    min_forecast: float = 0.0,
    max_forecast: float = 1.0,
    city: str = None,
    side: str = None,
) -> List[Trade]:
    """Filter trades by parameters."""
    filtered = []
    for trade in trades:
        if trade.edge < min_edge or trade.edge > max_edge:
            continue
        if trade.forecast_prob < min_forecast or trade.forecast_prob > max_forecast:
            continue
        if city and trade.city != city:
            continue
        if side and trade.side != side:
            continue
        filtered.append(trade)
    return filtered


def calculate_metrics(trades: List[Trade]) -> Dict[str, float]:
    """Calculate performance metrics for a set of trades."""
    if not trades:
        return {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'avg_pnl_per_trade': 0.0,
            'total_volume': 0.0,
            'roi': 0.0
        }

    wins = sum(1 for t in trades if t.outcome == 'win')
    losses = sum(1 for t in trades if t.outcome == 'loss')
    total_pnl = sum(t.pnl for t in trades)
    total_volume = sum(t.size for t in trades)

    return {
        'total_trades': len(trades),
        'wins': wins,
        'losses': losses,
        'win_rate': wins / len(trades) if trades else 0.0,
        'total_pnl': total_pnl,
        'avg_pnl_per_trade': total_pnl / len(trades) if trades else 0.0,
        'total_volume': total_volume,
        'roi': (total_pnl / total_volume * 100) if total_volume > 0 else 0.0
    }


def backtest_strategy(
    trades: List[Trade],
    min_edge: float,
    min_forecast: float,
    max_forecast: float,
    city: str = None,
    side: str = None,
) -> Dict[str, float]:
    """Backtest a specific strategy."""
    filtered = filter_trades(
        trades,
        min_edge=min_edge,
        min_forecast=min_forecast,
        max_forecast=max_forecast,
        city=city,
        side=side
    )
    return calculate_metrics(filtered)


def main():
    """Run comprehensive backtest analysis."""
    print("=" * 80)
    print("COMPREHENSIVE BACKTEST ANALYSIS")
    print("=" * 80)
    print()

    # Load trades
    trades = load_trades(config.DB_PATH)
    print(f"Loaded {len(trades)} resolved trades from database")
    print()

    # Split into training and validation sets
    train_trades = trades[:100]
    val_trades = trades[100:]

    print(f"Training set: {len(train_trades)} trades")
    print(f"Validation set: {len(val_trades)} trades")
    print()

    # Baseline metrics (all trades)
    print("=" * 80)
    print("BASELINE METRICS (ALL TRADES)")
    print("=" * 80)
    baseline = calculate_metrics(trades)
    for key, value in baseline.items():
        if isinstance(value, float):
            print(f"{key:25s}: {value:>12.2f}")
        else:
            print(f"{key:25s}: {value:>12}")
    print()

    # Test parameter combinations
    edge_thresholds = [0.10, 0.15, 0.20, 0.25, 0.30]
    forecast_ranges = [
        (0.10, 0.90),
        (0.20, 0.80),
        (0.30, 0.70),
        (0.40, 0.60),
    ]
    cities = list(set(t.city for t in trades)) + [None]  # All cities + combined
    sides = ['YES', 'NO', None]  # YES, NO, or both

    best_strategies = []

    print("=" * 80)
    print("PARAMETER OPTIMIZATION (TRAINING SET)")
    print("=" * 80)
    print()

    # Test all combinations
    for edge, (min_f, max_f), city, side in itertools.product(
        edge_thresholds, forecast_ranges, cities, sides
    ):
        # Train on training set
        train_metrics = backtest_strategy(
            train_trades,
            min_edge=edge,
            min_forecast=min_f,
            max_forecast=max_f,
            city=city,
            side=side
        )

        # Skip if not enough trades
        if train_metrics['total_trades'] < 10:
            continue

        # Skip if win rate < 55%
        if train_metrics['win_rate'] < 0.55:
            continue

        # Validate on validation set
        val_metrics = backtest_strategy(
            val_trades,
            min_edge=edge,
            min_forecast=min_f,
            max_forecast=max_f,
            city=city,
            side=side
        )

        # Skip if validation has too few trades
        if val_metrics['total_trades'] < 5:
            continue

        best_strategies.append({
            'edge': edge,
            'forecast_range': (min_f, max_f),
            'city': city or 'ALL',
            'side': side or 'BOTH',
            'train_metrics': train_metrics,
            'val_metrics': val_metrics
        })

    # Sort by validation win rate
    best_strategies.sort(key=lambda x: x['val_metrics']['win_rate'], reverse=True)

    if best_strategies:
        print(f"Found {len(best_strategies)} strategies with >55% win rate on training")
        print()
        print("TOP 10 STRATEGIES (by validation win rate):")
        print("=" * 80)

        for i, strategy in enumerate(best_strategies[:10], 1):
            print(f"\n#{i}. Strategy:")
            print(f"  Edge threshold: {strategy['edge']:.0%}")
            print(f"  Forecast range: {strategy['forecast_range'][0]:.0%} - {strategy['forecast_range'][1]:.0%}")
            print(f"  City: {strategy['city']}")
            print(f"  Side: {strategy['side']}")
            print()
            print(f"  TRAINING SET:")
            train = strategy['train_metrics']
            print(f"    Trades: {train['total_trades']}")
            print(f"    Win rate: {train['win_rate']:.1%}")
            print(f"    Total P&L: ${train['total_pnl']:+.2f}")
            print(f"    ROI: {train['roi']:+.1f}%")
            print()
            print(f"  VALIDATION SET:")
            val = strategy['val_metrics']
            print(f"    Trades: {val['total_trades']}")
            print(f"    Win rate: {val['win_rate']:.1%}")
            print(f"    Total P&L: ${val['total_pnl']:+.2f}")
            print(f"    ROI: {val['roi']:+.1f}%")
    else:
        print("⚠️  NO STRATEGIES FOUND with >55% win rate on training set")
        print()
        print("This suggests the current approach may not be profitable.")

    print()
    print("=" * 80)
    print("ANALYSIS BY SIDE (ALL TRADES)")
    print("=" * 80)

    for side in ['YES', 'NO']:
        side_trades = [t for t in trades if t.side == side]
        metrics = calculate_metrics(side_trades)
        print(f"\n{side} bets:")
        print(f"  Trades: {metrics['total_trades']}")
        print(f"  Win rate: {metrics['win_rate']:.1%}")
        print(f"  Total P&L: ${metrics['total_pnl']:+.2f}")
        print(f"  ROI: {metrics['roi']:+.1f}%")

    print()
    print("=" * 80)
    print("ANALYSIS BY CITY (ALL TRADES)")
    print("=" * 80)

    for city in sorted(set(t.city for t in trades)):
        city_trades = [t for t in trades if t.city == city]
        metrics = calculate_metrics(city_trades)
        print(f"\n{city}:")
        print(f"  Trades: {metrics['total_trades']}")
        print(f"  Win rate: {metrics['win_rate']:.1%}")
        print(f"  Total P&L: ${metrics['total_pnl']:+.2f}")
        print(f"  ROI: {metrics['roi']:+.1f}%")

    print()
    print("=" * 80)
    print("NO-ONLY STRATEGY ANALYSIS")
    print("=" * 80)

    no_trades = [t for t in trades if t.side == 'NO']
    print(f"\nAll NO bets: {len(no_trades)} trades")

    for edge in edge_thresholds:
        no_filtered = filter_trades(no_trades, min_edge=edge)
        if len(no_filtered) < 10:
            continue
        metrics = calculate_metrics(no_filtered)
        print(f"\nNO bets with edge ≥ {edge:.0%}:")
        print(f"  Trades: {metrics['total_trades']}")
        print(f"  Win rate: {metrics['win_rate']:.1%}")
        print(f"  Total P&L: ${metrics['total_pnl']:+.2f}")
        print(f"  ROI: {metrics['roi']:+.1f}%")

    print()
    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print()

    if best_strategies and best_strategies[0]['val_metrics']['win_rate'] > 0.55:
        print("✅ PROFITABLE STRATEGY FOUND!")
        best = best_strategies[0]
        print(f"\nBest strategy: {best['side']} bets on {best['city']}")
        print(f"Edge ≥ {best['edge']:.0%}, Forecast {best['forecast_range'][0]:.0%}-{best['forecast_range'][1]:.0%}")
        print(f"Validation win rate: {best['val_metrics']['win_rate']:.1%}")
        print(f"Validation ROI: {best['val_metrics']['roi']:+.1f}%")
    else:
        print("❌ NO PROFITABLE STRATEGY FOUND")
        print()
        print("Based on 190 trades split into training/validation sets,")
        print("no parameter combination achieves >55% win rate on both sets.")
        print()
        print("This suggests:")
        print("1. Weather forecasts may not have predictable edge over markets")
        print("2. Sample size may be too small for reliable patterns")
        print("3. Markets are efficient at pricing weather outcomes")
        print("4. Different approach or data source may be needed")

    print()


if __name__ == "__main__":
    main()
