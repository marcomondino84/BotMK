"""
Clase base abstracta para el desarrollo de estrategias cuantitativas.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd


class BaseStrategy(ABC):
    """
    Interfaz unificada que todas las estrategias deben implementar.
    Permite intercambiar lógicas en tiempo real o en backtesting sin modificar el core.
    """

    def __init__(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        Analiza las velas (OHLCV) o datos de mercado y retorna una señal:
        {
            "action": "BUY" | "SELL" | "HOLD",
            "entry_price": float,
            "stop_loss": float,
            "take_profit": float,
            "metadata": dict
        }
        """
        pass
