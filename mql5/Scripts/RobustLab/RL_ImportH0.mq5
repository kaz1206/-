//+------------------------------------------------------------------+
//| RL_ImportH0.mq5                                                  |
//| P10 fidelity check: builds the custom symbol RL_H0_EURUSD from   |
//| synthetic M1 bars (Common\Files\RL_H0_EURUSD_M1.csv, written by  |
//| `rlab null fidelity-export`). Copies the properties of EURUSD,   |
//| fixes the spread at 10 points. Synthetic data only: never trade. |
//| API: CustomSymbolCreate / CustomRatesReplace / SymbolExist       |
//| (MQL5 reference; confirmation level B, checked on the terminal). |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.00"

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
  }
//+------------------------------------------------------------------+
