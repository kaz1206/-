//+------------------------------------------------------------------+
//| RL_ImportH0.mq5                                                  |
//| P10 fidelity check: builds the custom symbol RL_H0_EURUSD from   |
//| synthetic M1 bars (Common\Files\RL_H0_EURUSD_M1.csv, written by  |
//| `rlab null fidelity-export`). Copies the properties of EURUSD,   |
//| fixes the spread at 10 points. Synthetic data only: never trade. |
//| v1.01: CustomSymbolCreate did NOT copy the base / profit currency |
//| (they came from the custom name: "RL_" / "H0_") and left the tick |
//| size / value at 0, so every deal profit was 0 (found 2026-10-12). |
//| The currencies and the tick size are now read from the origin    |
//| symbol at run time and set explicitly (no hand-typed values).    |
//| API: CustomSymbolCreate / CustomRatesReplace / SymbolExist       |
//| (MQL5 reference; confirmation level B, checked on the terminal). |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.01"

input string InpFile   = "RL_H0_EURUSD_M1.csv";
input string InpSymbol = "RL_H0_EURUSD";
input string InpOrigin = "EURUSD";

void OnStart()
  {
   bool is_custom = false;
   if(SymbolExist(InpSymbol, is_custom))
     {
      if(!is_custom)
        {
         Print("RL_ImportH0: a non-custom symbol named ", InpSymbol, " exists; stopped.");
         return;
        }
     }
   else if(!CustomSymbolCreate(InpSymbol, "RobustLab", InpOrigin))
     {
      Print("RL_ImportH0: CustomSymbolCreate failed, error ", GetLastError());
      return;
     }
   CustomSymbolSetInteger(InpSymbol, SYMBOL_SPREAD_FLOAT, false);
   CustomSymbolSetInteger(InpSymbol, SYMBOL_SPREAD, 10);
   // copy from the origin what CustomSymbolCreate did not (evidence: RL_SymbolDiff, 2026-10-12)
   bool okb = CustomSymbolSetString(InpSymbol, SYMBOL_CURRENCY_BASE, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_BASE));
   bool okp = CustomSymbolSetString(InpSymbol, SYMBOL_CURRENCY_PROFIT, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_PROFIT));
   bool okm = CustomSymbolSetString(InpSymbol, SYMBOL_CURRENCY_MARGIN, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_MARGIN));
   bool okt = CustomSymbolSetDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE, SymbolInfoDouble(InpOrigin, SYMBOL_TRADE_TICK_SIZE));
   PrintFormat("RL_ImportH0: set currencies base %s profit %s margin %s, tick size %.10g (ok %d %d %d %d)",
               SymbolInfoString(InpSymbol, SYMBOL_CURRENCY_BASE), SymbolInfoString(InpSymbol, SYMBOL_CURRENCY_PROFIT),
               SymbolInfoString(InpSymbol, SYMBOL_CURRENCY_MARGIN), SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE),
               okb, okp, okm, okt);

   int h = FileOpen(InpFile, FILE_READ | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      Print("RL_ImportH0: cannot open Common\\Files\\", InpFile, ", error ", GetLastError());
      return;
     }
   MqlRates rates[];
   int n = 0;
   ArrayResize(rates, 0, 400000);
   while(!FileIsEnding(h))
     {
      string t = FileReadString(h);
      if(t == "")
         break;
      MqlRates r;
      r.time        = (datetime)StringToInteger(t);
      r.open        = StringToDouble(FileReadString(h));
      r.high        = StringToDouble(FileReadString(h));
      r.low         = StringToDouble(FileReadString(h));
      r.close       = StringToDouble(FileReadString(h));
      r.tick_volume = (long)StringToInteger(FileReadString(h));
      r.spread      = (int)StringToInteger(FileReadString(h));
      r.real_volume = 0;
      ArrayResize(rates, n + 1, 400000);
      rates[n++] = r;
     }
   FileClose(h);
   if(n == 0)
     {
      Print("RL_ImportH0: no rows read");
      return;
     }
   int done = CustomRatesReplace(InpSymbol, rates[0].time, rates[n - 1].time, rates);
   SymbolSelect(InpSymbol, true);
   PrintFormat("RL_ImportH0: read %d M1 bars, CustomRatesReplace returned %d, from %s to %s", n, done,
               TimeToString(rates[0].time), TimeToString(rates[n - 1].time));
   double pa = 0.0, pb = 0.0;
   bool ca = OrderCalcProfit(ORDER_TYPE_BUY, InpOrigin, 0.01, 1.10000, 1.10100, pa);
   bool cb = OrderCalcProfit(ORDER_TYPE_BUY, InpSymbol, 0.01, 1.10000, 1.10100, pb);
   PrintFormat("RL_ImportH0: check OrderCalcProfit 0.01 lot +100 points: %s %s %.2f | %s %s %.2f (account currency %s); "
               "tick value %s %.10g | %s %.10g", InpOrigin, ca ? "ok" : "fail", pa, InpSymbol, cb ? "ok" : "fail", pb,
               AccountInfoString(ACCOUNT_CURRENCY), InpOrigin, SymbolInfoDouble(InpOrigin, SYMBOL_TRADE_TICK_VALUE),
               InpSymbol, SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_VALUE));
  }
//+------------------------------------------------------------------+
