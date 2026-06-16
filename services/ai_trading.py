import os
import json
from dotenv import load_dotenv
from groq import Groq

from services.database import Database

load_dotenv()


class AITradingAnalyzer:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        if self.api_key:
            self.client = Groq(api_key=self.api_key)
            self.model = "llama-3.3-70b-versatile"
            print("✅ Groq AI инициализирован")
        else:
            self.client = None
            print("⚠️ GROQ_API_KEY не найден. AI отключён.")

    def analyze(self) -> str:
        """Основной AI-анализ статистики и последних сделок."""
        db = Database()
        stats = db.get_stats()
        closed = db.get_closed_trades(limit=15)

        if not closed:
            return (
                "🤖 *AI-анализ*\n\n"
                "Пока нет закрытых сделок для анализа.\n"
                "Торгуй больше — AI найдёт паттерны! 📈"
            )

        if not self.client:
            return self._fallback_analysis(stats)

        trades_for_ai = []
        for trade in closed:
            trades_for_ai.append({
                "symbol": trade.get("symbol", ""),
                "side": trade.get("side", ""),
                "pnl": trade.get("realized_pnl", 0),
                "entry": trade.get("entry_price", 0),
                "exit": trade.get("exit_price", 0),
                "comment": trade.get("comment", ""),
                "time": trade.get("close_time", "")
            })

        prompt = f"""Ты — трейдер-ментор. Проанализируй статистику и последние сделки на русском языке.
Будь прямым, конкретным, без воды. Используй эмодзи, но не markdown.

СТАТИСТИКА:
{json.dumps(stats, ensure_ascii=False, indent=2)}

ПОСЛЕДНИЕ СДЕЛКИ (JSON):
{json.dumps(trades_for_ai, ensure_ascii=False, indent=2)}

Выведи ответ строго по пунктам:
1. 📊 ОБЩАЯ ОЦЕНКА
2. 🔍 ГЛАВНЫЕ ОШИБКИ
3. ✅ СИЛЬНЫЕ СТОРОНЫ
4. ⚠️ РИСК-МЕНЕДЖМЕНТ
5. 🧠 ПСИХОЛОГИЯ
6. 🎯 ПЛАН ДЕЙСТВИЙ (2-3 шага)"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Ты строгий, но полезный trading coach."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1500
            )
            return response.choices[0].message.content + "\n\n🤖 Анализ от Groq (Llama 3.3 70B). Не финансовая рекомендация."
        except Exception as e:
            print(f"❌ Ошибка Groq: {e}")
            return f"⚠️ Ошибка AI: {e}\n\n{self._fallback_analysis(stats)}"

    def analyze_raw(self, prompt: str) -> str:
        """Отправка произвольного промпта в Groq (для вопросов, обзора рынка, трендов и т.д.)"""
        if not self.client:
            return "⚠️ AI недоступен. Проверь GROQ_API_KEY."
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        "Ты — строгий, конкретный трейдер-ментор с опытом на крипторынке. "
                        "Отвечаешь на русском языке, коротко и по делу. "
                        "Не используешь общие фразы и философию — только конкретику: цифры, уровни, чёткие выводы. "
                        "Используй эмодзи, но без markdown-разметки звёздочками."
                    )},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=1200
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"❌ Ошибка Groq (analyze_raw): {e}")
            return f"⚠️ Ошибка AI: {e}"

    def _fallback_analysis(self, stats: dict = None) -> str:
        """Базовый rule-based анализ (если AI недоступен)."""
        if not stats or stats.get('total_trades', 0) == 0:
            return (
                "🤖 *AI-анализ*\n\n"
                "Пока нет закрытых сделок для анализа.\n"
                "Торгуй больше — AI найдёт паттерны! 📈"
            )

        observations = []
        suggestions = []

        if stats['win_rate'] >= 60:
            observations.append(f"✅ Win Rate {stats['win_rate']}% — хороший результат")
        elif stats['win_rate'] >= 40:
            observations.append(f"⚠️ Win Rate {stats['win_rate']}% — есть куда расти")
        else:
            observations.append(f"❌ Win Rate {stats['win_rate']}% — нужна работа над стратегией")
            suggestions.append("Пересмотри критерии входа в сделку")

        if stats['avg_profit'] > 0 and stats['avg_loss'] < 0:
            rr = abs(stats['avg_profit'] / stats['avg_loss']) if stats['avg_loss'] != 0 else 0
            if rr >= 2:
                observations.append(f"✅ R/R = {rr:.1f} — отличное соотношение риск/прибыль")
            elif rr >= 1:
                observations.append(f"⚠️ R/R = {rr:.1f} — соотношение приемлемое")
            else:
                observations.append(f"❌ R/R = {rr:.1f} — прибыль меньше убытка")
                suggestions.append("Увеличь тейк-профит или уменьши стоп-лосс")

        if stats['total_pnl'] > 0:
            observations.append(f"✅ Общий PNL положительный: +${stats['total_pnl']:.2f}")
        else:
            observations.append(f"❌ Общий PNL отрицательный: ${stats['total_pnl']:.2f}")
            suggestions.append("Работай над управлением рисками")

        db = Database()
        closed = db.get_closed_trades(limit=1000)
        trades_with_comments = [t for t in closed if t.get('comment')]
        comment_rate = len(trades_with_comments) / len(closed) * 100 if closed else 0
        if comment_rate < 50:
            suggestions.append("Добавляй больше комментариев к сделкам — это помогает найти паттерны")

        result = "🤖 *AI-анализ торговли*\n\n"
        result += f"📊 Проанализировано сделок: {stats['total_trades']}\n\n"

        if observations:
            result += "🔍 *Наблюдения:*\n"
            result += "\n".join(observations) + "\n\n"

        if suggestions:
            result += "💡 *Рекомендации:*\n"
            result += "\n".join(f"• {s}" for s in suggestions) + "\n\n"

        result += "_В будущем здесь будет полноценный AI с анализом паттернов_ 🚀"
        return result

    @staticmethod
    def prepare_data_for_ai() -> dict:
        """Подготовить данные для будущего AI-модуля."""
        db = Database()
        closed = db.get_closed_trades(limit=1000)
        stats = db.get_stats()

        symbols = {}
        for trade in closed:
            symbol = trade.get('symbol', 'UNKNOWN')
            if symbol not in symbols:
                symbols[symbol] = {'count': 0, 'total_pnl': 0.0, 'wins': 0}
            pnl = float(trade.get('realized_pnl', 0))
            symbols[symbol]['count'] += 1
            symbols[symbol]['total_pnl'] += pnl
            if pnl > 0:
                symbols[symbol]['wins'] += 1

        return {
            'stats': stats,
            'symbols': symbols,
            'total_closed': len(closed),
            'trades_with_comments': len([t for t in closed if t.get('comment')])
        }