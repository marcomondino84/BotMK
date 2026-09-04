"""
Persistencia con SQLite y SQLAlchemy.
Gestiona el estado del bot, registro de trades, logs del sistema y estadísticas del Circuit Breaker.
"""

from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, List, Optional

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Boolean,
    DateTime,
    desc
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from config import config

# Asegurar creación de carpeta data/
db_dir = Path("data")
db_dir.mkdir(exist_ok=True)

Base = declarative_base()
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class TradeRecord(Base):
    """Historial de operaciones de trading."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String(64), unique=True, index=True, nullable=True)
    symbol = Column(String(32), index=True, nullable=False)
    side = Column(String(8), nullable=False)  # BUY (Long) o SELL (Short)
    amount = Column(Float, nullable=False)
    leverage = Column(Integer, default=5)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)
    tp_price = Column(Float, nullable=True)
    pnl_usd = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    status = Column(String(16), default="OPEN", index=True)  # OPEN, CLOSED, CANCELLED
    opened_at = Column(DateTime, default=datetime.now)
    closed_at = Column(DateTime, nullable=True)


class BotStateRecord(Base):
    """Estado persistente del bot y métricas para el Circuit Breaker."""
    __tablename__ = "bot_state"

    id = Column(Integer, primary_key=True, default=1)
    is_active = Column(Boolean, default=True)
    consecutive_losses = Column(Integer, default=0)
    circuit_breaker_triggered = Column(Boolean, default=False)
    circuit_breaker_reason = Column(String(255), default="")
    daily_pnl_usd = Column(Float, default=0.0)
    scanner_active = Column(Boolean, default=False)
    scanner_mode = Column(String(32), default="watchlist")
    last_reset_date = Column(String(10), default=lambda: str(date.today()))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LogRecord(Base):
    """Logs del sistema accesibles desde el panel web."""
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(16), default="INFO")
    message = Column(String(512), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class WatchlistRecord(Base):
    """Tickers personalizados monitoreados por el bot."""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    category = Column(String(32), default="crypto")
    symbol = Column(String(64), nullable=False)
    target_sl = Column(Float, default=1.5)
    added_at = Column(DateTime, default=datetime.now)


def init_db():
    """Crea todas las tablas si no existen e inicializa el estado y la watchlist."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        state = session.query(BotStateRecord).filter_by(id=1).first()
        if not state:
            state = BotStateRecord(
                id=1,
                is_active=True,
                consecutive_losses=0,
                circuit_breaker_triggered=False,
                circuit_breaker_reason=""
            )
            session.add(state)
            session.commit()

        # Poblar watchlist inicial si está vacía
        w_count = session.query(WatchlistRecord).count()
        if w_count == 0:
            from config import ASSET_CATALOG
            for tick, data in ASSET_CATALOG.items():
                session.add(WatchlistRecord(
                    ticker=tick,
                    name=data["name"],
                    category=data["category"],
                    symbol=data["symbol"],
                    target_sl=data.get("target_sl", 1.5)
                ))
            session.commit()


def get_db_session() -> Session:
    """Retorna una sesión local de base de datos."""
    return SessionLocal()


def get_bot_state() -> Dict[str, Any]:
    """Obtiene el estado actual del bot."""
    with SessionLocal() as session:
        state = session.query(BotStateRecord).filter_by(id=1).first()
        if not state:
            init_db()
            state = session.query(BotStateRecord).filter_by(id=1).first()
        return {
            "is_active": state.is_active,
            "consecutive_losses": state.consecutive_losses,
            "circuit_breaker_triggered": state.circuit_breaker_triggered,
            "circuit_breaker_reason": state.circuit_breaker_reason,
            "daily_pnl_usd": state.daily_pnl_usd,
            "scanner_active": state.scanner_active if state.scanner_active is not None else False,
            "scanner_mode": state.scanner_mode or "watchlist"
        }


def set_bot_active(active: bool, reason: str = "") -> bool:
    """Activa o desactiva el bot (ON/OFF switch)."""
    with SessionLocal() as session:
        state = session.query(BotStateRecord).filter_by(id=1).first()
        if state:
            state.is_active = active
            if not active and reason:
                state.circuit_breaker_triggered = True
                state.circuit_breaker_reason = reason
            elif active:
                state.circuit_breaker_triggered = False
                state.circuit_breaker_reason = ""
                state.consecutive_losses = 0
            session.commit()
            return state.is_active
    return False


def set_scanner_state(active: bool, mode: Optional[str] = None) -> bool:
    """Persiste el estado del scanner y opcionalmente su modo en la base de datos."""
    with SessionLocal() as session:
        state = session.query(BotStateRecord).filter_by(id=1).first()
        if state:
            state.scanner_active = active
            if mode:
                state.scanner_mode = mode
            session.commit()
            return state.scanner_active
    return False


def record_trade_opened(
    order_id: str,
    symbol: str,
    side: str,
    amount: float,
    entry_price: float,
    leverage: int,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None
) -> int:
    """Registra la apertura de un trade."""
    with SessionLocal() as session:
        trade = TradeRecord(
            order_id=order_id,
            symbol=symbol,
            side=side.upper(),
            amount=amount,
            entry_price=entry_price,
            leverage=leverage,
            sl_price=sl_price,
            tp_price=tp_price,
            status="OPEN",
            opened_at=datetime.now()
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade.id


def record_trade_closed(
    order_id: str,
    exit_price: float,
    pnl_usd: float
) -> Optional[TradeRecord]:
    """
    Registra el cierre de un trade, actualiza el PnL y evalúa el Circuit Breaker.
    """
    with SessionLocal() as session:
        trade = session.query(TradeRecord).filter_by(order_id=order_id).first()
        if not trade:
            # Buscar el último trade abierto con ese símbolo si el order_id difiere
            trade = session.query(TradeRecord).filter_by(status="OPEN").order_by(desc(TradeRecord.id)).first()

        if trade:
            trade.exit_price = exit_price
            trade.pnl_usd = pnl_usd
            trade.status = "CLOSED"
            trade.closed_at = datetime.now()
            if trade.entry_price > 0:
                mult = 1 if trade.side == "BUY" else -1
                trade.pnl_pct = mult * ((exit_price - trade.entry_price) / trade.entry_price) * 100 * trade.leverage

            # Actualizar estado del bot y evaluar Circuit Breaker
            state = session.query(BotStateRecord).filter_by(id=1).first()
            if state:
                state.daily_pnl_usd += pnl_usd
                if pnl_usd < 0:
                    state.consecutive_losses += 1
                    # Disparador 1: 3 pérdidas consecutivas
                    if state.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
                        state.is_active = False
                        state.circuit_breaker_triggered = True
                        state.circuit_breaker_reason = (
                            f"Circuit Breaker: {state.consecutive_losses} pérdidas consecutivas acumuladas."
                        )
                        add_system_log("WARNING", state.circuit_breaker_reason)
                else:
                    state.consecutive_losses = 0

            session.commit()
            return trade
    return None


def get_recent_trades(limit: int = 20) -> List[Dict[str, Any]]:
    """Obtiene los últimos trades cerrados o abiertos para la tabla del panel."""
    with SessionLocal() as session:
        trades = session.query(TradeRecord).order_by(desc(TradeRecord.id)).limit(limit).all()
        result = []
        for t in trades:
            notional = round(t.amount * t.entry_price, 2) if t.entry_price else 0.0
            margin = round(notional / t.leverage, 2) if t.leverage and t.leverage > 0 else notional
            result.append({
                "id": t.id,
                "order_id": t.order_id,
                "symbol": t.symbol,
                "side": t.side,
                "amount": t.amount,
                "leverage": t.leverage or 1,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "sl_price": t.sl_price,
                "tp_price": t.tp_price,
                "margin_usdt": margin,
                "notional_usdt": notional,
                "pnl_usd": round(t.pnl_usd, 2),
                "pnl_pct": round(t.pnl_pct, 2),
                "status": t.status,
                "opened_at": t.opened_at.strftime("%Y-%m-%d %H:%M:%S") if t.opened_at else "",
                "closed_at": t.closed_at.strftime("%Y-%m-%d %H:%M:%S") if t.closed_at else ""
            })
        return result


def get_performance_stats() -> Dict[str, Any]:
    """Calcula las métricas cuantitativas clave y detalladas para el panel y modal de estadísticas."""
    with SessionLocal() as session:
        trades = session.query(TradeRecord).filter(TradeRecord.status == "CLOSED").all()
        total_closed = len(trades)
        wins = sum(1 for t in trades if t.pnl_usd > 0)
        losses = sum(1 for t in trades if t.pnl_usd < 0)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
        total_pnl = sum(t.pnl_usd for t in trades)

        gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gross_loss = sum(abs(t.pnl_usd) for t in trades if t.pnl_usd < 0)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)

        best_trade = max((t.pnl_usd for t in trades), default=0.0)
        worst_trade = min((t.pnl_usd for t in trades), default=0.0)
        avg_trade_pnl = round(total_pnl / total_closed, 2) if total_closed > 0 else 0.0
        total_volume_usd = sum((t.amount * t.entry_price) for t in trades if t.entry_price)

        # Trades cerrados hoy
        today_str = date.today().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if t.closed_at and t.closed_at.strftime("%Y-%m-%d") == today_str]
        daily_pnl = sum(t.pnl_usd for t in today_trades)
        today_wins = sum(1 for t in today_trades if t.pnl_usd > 0)
        today_losses = sum(1 for t in today_trades if t.pnl_usd < 0)

        # Estado del bot
        state = session.query(BotStateRecord).filter_by(id=1).first()
        is_active = state.is_active if state else True
        consecutive_losses = state.consecutive_losses if state else 0
        cb_triggered = state.circuit_breaker_triggered if state else False
        cb_reason = state.circuit_breaker_reason if state else ""

        return {
            "total_trades": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "daily_pnl": round(daily_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": profit_factor,
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "avg_trade_pnl": avg_trade_pnl,
            "total_volume_usd": round(total_volume_usd, 2),
            "today_trades": len(today_trades),
            "today_wins": today_wins,
            "today_losses": today_losses,
            "consecutive_losses": consecutive_losses,
            "is_active": is_active,
            "circuit_breaker_triggered": cb_triggered,
            "circuit_breaker_reason": cb_reason
        }


def add_system_log(level: str, message: str):
    """Guarda un log en la base de datos para visualización web."""
    try:
        with SessionLocal() as session:
            log_item = LogRecord(level=level.upper(), message=message, created_at=datetime.now())
            session.add(log_item)
            session.commit()
    except Exception:
        pass


def get_recent_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Obtiene los logs más recientes para la consola del panel web."""
    with SessionLocal() as session:
        logs = session.query(LogRecord).order_by(desc(LogRecord.id)).limit(limit).all()
        return [
            {
                "id": l.id,
                "level": l.level,
                "message": l.message,
                "time": l.created_at.strftime("%H:%M:%S")
            }
            for l in reversed(logs)
        ]


def get_db_watchlist() -> List[Dict[str, Any]]:
    """Retorna la lista de tickers monitoreados guardados en la base de datos."""
    with SessionLocal() as session:
        items = session.query(WatchlistRecord).order_by(WatchlistRecord.id.asc()).all()
        return [
            {
                "ticker": w.ticker,
                "name": w.name,
                "category": w.category,
                "symbol": w.symbol,
                "target_sl": w.target_sl
            }
            for w in items
        ]


def add_db_watchlist_ticker(ticker: str, name: str, category: str, symbol: str, target_sl: float = 1.5) -> bool:
    """Agrega un ticker a la watchlist de SQLite."""
    with SessionLocal() as session:
        clean_ticker = ticker.strip().upper()
        existing = session.query(WatchlistRecord).filter_by(ticker=clean_ticker).first()
        if existing:
            return False

        item = WatchlistRecord(
            ticker=clean_ticker,
            name=name,
            category=category,
            symbol=symbol,
            target_sl=target_sl
        )
        session.add(item)
        session.commit()
        return True


def delete_db_watchlist_ticker(ticker: str) -> bool:
    """Elimina un ticker de la watchlist."""
    with SessionLocal() as session:
        clean_ticker = ticker.strip().upper()
        item = session.query(WatchlistRecord).filter_by(ticker=clean_ticker).first()
        if item:
            session.delete(item)
            session.commit()
            return True
        return False
