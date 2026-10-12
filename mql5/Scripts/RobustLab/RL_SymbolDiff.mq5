//+------------------------------------------------------------------+
//| RL_SymbolDiff.mq5                                                |
//| P10 fidelity check, diagnosis only: prints the profit-related    |
//| properties of EURUSD and EURUSD_RLH0 side by side (Experts log). |
//| Changes nothing.                                                 |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.00"

input string InpA = "EURUSD";
input string InpB = "EURUSD_RLH0";

void PD(string name, ENUM_SYMBOL_INFO_DOUBLE p)
  {
   PrintFormat("RL_SymbolDiff: %-28s %s=%.10g  %s=%.10g", name, InpA, SymbolInfoDouble(InpA, p), InpB,
               SymbolInfoDouble(InpB, p));
  }

void PI(string name, ENUM_SYMBOL_INFO_INTEGER p)
  {
   PrintFormat("RL_SymbolDiff: %-28s %s=%I64d  %s=%I64d", name, InpA, SymbolInfoInteger(InpA, p), InpB,
               SymbolInfoInteger(InpB, p));
  }

void PS(string name, ENUM_SYMBOL_INFO_STRING p)
  {
   PrintFormat("RL_SymbolDiff: %-28s %s=%s  %s=%s", name, InpA, SymbolInfoString(InpA, p), InpB,
               SymbolInfoString(InpB, p));
  }

void OnStart()
  {
   SymbolSelect(InpA, true);
   SymbolSelect(InpB, true);
   PI("SYMBOL_CUSTOM", SYMBOL_CUSTOM);
   PI("SYMBOL_TRADE_CALC_MODE", SYMBOL_TRADE_CALC_MODE);
   PI("SYMBOL_TRADE_MODE", SYMBOL_TRADE_MODE);
   PI("SYMBOL_DIGITS", SYMBOL_DIGITS);
   PI("SYMBOL_SPREAD", SYMBOL_SPREAD);
   PI("SYMBOL_SPREAD_FLOAT", SYMBOL_SPREAD_FLOAT);
   PD("SYMBOL_POINT", SYMBOL_POINT);
   PD("SYMBOL_TRADE_CONTRACT_SIZE", SYMBOL_TRADE_CONTRACT_SIZE);
   PD("SYMBOL_TRADE_TICK_SIZE", SYMBOL_TRADE_TICK_SIZE);
   PD("SYMBOL_TRADE_TICK_VALUE", SYMBOL_TRADE_TICK_VALUE);
   PD("SYMBOL_TRADE_TICK_VALUE_PROFIT", SYMBOL_TRADE_TICK_VALUE_PROFIT);
   PD("SYMBOL_TRADE_TICK_VALUE_LOSS", SYMBOL_TRADE_TICK_VALUE_LOSS);
   PD("SYMBOL_VOLUME_MIN", SYMBOL_VOLUME_MIN);
   PD("SYMBOL_VOLUME_STEP", SYMBOL_VOLUME_STEP);
   PD("SYMBOL_MARGIN_INITIAL", SYMBOL_MARGIN_INITIAL);
   PD("SYMBOL_SWAP_LONG", SYMBOL_SWAP_LONG);
   PD("SYMBOL_SWAP_SHORT", SYMBOL_SWAP_SHORT);
   PI("SYMBOL_SWAP_MODE", SYMBOL_SWAP_MODE);
   PS("SYMBOL_CURRENCY_BASE", SYMBOL_CURRENCY_BASE);
   PS("SYMBOL_CURRENCY_PROFIT", SYMBOL_CURRENCY_PROFIT);
   PS("SYMBOL_CURRENCY_MARGIN", SYMBOL_CURRENCY_MARGIN);
   PS("SYMBOL_PATH", SYMBOL_PATH);
   double p = 0.0;
   bool okA = OrderCalcProfit(ORDER_TYPE_BUY, InpA, 0.01, 1.10000, 1.10100, p);
   PrintFormat("RL_SymbolDiff: OrderCalcProfit 0.01 lot +100 points  %s=%s %.2f", InpA, okA ? "ok" : "fail", p);
   p = 0.0;
   bool okB = OrderCalcProfit(ORDER_TYPE_BUY, InpB, 0.01, 1.10000, 1.10100, p);
   PrintFormat("RL_SymbolDiff: OrderCalcProfit 0.01 lot +100 points  %s=%s %.2f (error %d)", InpB, okB ? "ok" : "fail",
               p, GetLastError());
  }
//+------------------------------------------------------------------+
