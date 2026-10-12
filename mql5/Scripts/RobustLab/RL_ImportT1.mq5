//+------------------------------------------------------------------+
//| RL_ImportT1.mq5                                                  |
//| Study A fidelity check (docs/research/study_a/SPEC.md section 3):       |
//| builds two custom symbols from synthetic M1 bars written by      |
//| `rlab studya fidelity-export` in Common\Files:                   |
//|   RL_T1_H0_M1.csv -> EURUSD_RLT1 (no edge)                       |
//|   RL_T1_E1_M1.csv -> EURUSD_RLT2 (edge E1)                       |
//| Same steps as RL_ImportH0 v1.02 (P10, left unchanged): properties |
//| from EURUSD, currencies / tick size set explicitly, spread fixed  |
//| at 10 points; in addition the SWAPS ARE SET TO 0 so that the     |
//| tester's P&L can be compared with the swap-free T1 model (section 3.3). |
//| v1.01 (SPEC section 12.3): also writes the stop-out and margin    |
//| settings to Common\Files\RL_T1_account.json (stop-out mode and  |
//| level, trade mode, symbol calc mode, margin rates, currencies).  |
//| No login, name or server is written.                             |
//| Synthetic data only: never trade on these symbols.               |
//| API: CustomSymbolCreate / CustomSymbolSetDouble / CustomRatesReplace |
//| (MQL5 reference; confirmation level B, checked on the terminal). |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.01"

input string InpFile1   = "RL_T1_H0_M1.csv";
input string InpSymbol1 = "EURUSD_RLT1";   // the first 6 letters set the currencies of a FOREX symbol
input string InpFile2   = "RL_T1_E1_M1.csv";
input string InpSymbol2 = "EURUSD_RLT2";
input string InpOrigin  = "EURUSD";
input string InpSettings = "RL_T1_account.json";   // stop-out / margin settings for the comparison (no login)

bool Build(const string file, const string symbol)
  {
   bool is_custom = false;
   if(SymbolExist(symbol, is_custom))
     {
      if(!is_custom)
        {
         Print("RL_ImportT1: a non-custom symbol named ", symbol, " exists; stopped.");
         return false;
        }
     }
   else if(!CustomSymbolCreate(symbol, "RobustLab", InpOrigin))
     {
      Print("RL_ImportT1: CustomSymbolCreate failed for ", symbol, ", error ", GetLastError());
      return false;
     }
   CustomSymbolSetInteger(symbol, SYMBOL_SPREAD_FLOAT, false);
   CustomSymbolSetInteger(symbol, SYMBOL_SPREAD, 10);
   CustomSymbolSetString(symbol, SYMBOL_CURRENCY_BASE, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_BASE));
   CustomSymbolSetString(symbol, SYMBOL_CURRENCY_PROFIT, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_PROFIT));
   CustomSymbolSetString(symbol, SYMBOL_CURRENCY_MARGIN, SymbolInfoString(InpOrigin, SYMBOL_CURRENCY_MARGIN));
   CustomSymbolSetDouble(symbol, SYMBOL_TRADE_TICK_SIZE, SymbolInfoDouble(InpOrigin, SYMBOL_TRADE_TICK_SIZE));
   bool sl = CustomSymbolSetDouble(symbol, SYMBOL_SWAP_LONG, 0.0);
   bool ss = CustomSymbolSetDouble(symbol, SYMBOL_SWAP_SHORT, 0.0);
   PrintFormat("RL_ImportT1: %s currencies %s/%s margin %s, tick size %.10g, spread float %d spread %d, "
               "swap long %.4f short %.4f (set ok %d %d)", symbol, SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE),
               SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT), SymbolInfoString(symbol, SYMBOL_CURRENCY_MARGIN),
               SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE), (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD_FLOAT),
               (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD), SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG),
               SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT), sl, ss);

   int h = FileOpen(file, FILE_READ | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      Print("RL_ImportT1: cannot open Common\\Files\\", file, ", error ", GetLastError());
      return false;
     }
   MqlRates rates[];
   int n = 0;
   ArrayResize(rates, 0, 600000);
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
      ArrayResize(rates, n + 1, 600000);
      rates[n++] = r;
     }
   FileClose(h);
   if(n == 0)
     {
      Print("RL_ImportT1: no rows read from ", file);
      return false;
     }
   int done = CustomRatesReplace(symbol, rates[0].time, rates[n - 1].time, rates);
   SymbolSelect(symbol, true);
   PrintFormat("RL_ImportT1: %s read %d M1 bars, CustomRatesReplace returned %d, from %s to %s", symbol, n, done,
               TimeToString(rates[0].time), TimeToString(rates[n - 1].time));
   return done == n;
  }

string Num(const double v)
  {
   return DoubleToString(v, 8);
  }

string Str(const string v)
  {
   return "\"" + v + "\"";
  }

string SymbolJson(const string symbol)
  {
   double bi = 0, bm = 0, si = 0, sm = 0;
   bool okb = SymbolInfoMarginRate(symbol, ORDER_TYPE_BUY, bi, bm);
   bool oks = SymbolInfoMarginRate(symbol, ORDER_TYPE_SELL, si, sm);
   return Str(symbol) + ": {"
          + Str("calc_mode") + ": " + Str(EnumToString((ENUM_SYMBOL_CALC_MODE)SymbolInfoInteger(symbol, SYMBOL_TRADE_CALC_MODE))) + ", "
          + Str("contract_size") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE)) + ", "
          + Str("margin_initial") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_MARGIN_INITIAL)) + ", "
          + Str("margin_maintenance") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_MARGIN_MAINTENANCE)) + ", "
          + Str("margin_hedged") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_MARGIN_HEDGED)) + ", "
          + Str("margin_rate_ok") + ": " + ((okb && oks) ? "true" : "false") + ", "
          + Str("margin_rate_buy_initial") + ": " + Num(bi) + ", "
          + Str("margin_rate_buy_maintenance") + ": " + Num(bm) + ", "
          + Str("margin_rate_sell_initial") + ": " + Num(si) + ", "
          + Str("margin_rate_sell_maintenance") + ": " + Num(sm) + ", "
          + Str("currency_base") + ": " + Str(SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE)) + ", "
          + Str("currency_profit") + ": " + Str(SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT)) + ", "
          + Str("currency_margin") + ": " + Str(SymbolInfoString(symbol, SYMBOL_CURRENCY_MARGIN)) + ", "
          + Str("tick_size") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE)) + ", "
          + Str("tick_value") + ": " + Num(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE)) + ", "
          + Str("digits") + ": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + "}";
  }

bool WriteSettings()
  {
   string so_mode = EnumToString((ENUM_ACCOUNT_STOPOUT_MODE)AccountInfoInteger(ACCOUNT_MARGIN_SO_MODE));
   string json = "{" + Str("script") + ": " + Str("RL_ImportT1") + ", " + Str("version") + ": " + Str("1.01") + ", "
                 + Str("terminal_build") + ": " + IntegerToString(TerminalInfoInteger(TERMINAL_BUILD)) + ", "
                 + Str("account") + ": {"
                 + Str("currency") + ": " + Str(AccountInfoString(ACCOUNT_CURRENCY)) + ", "
                 + Str("leverage") + ": " + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) + ", "
                 + Str("trade_mode") + ": " + Str(EnumToString((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE))) + ", "
                 + Str("margin_mode") + ": " + Str(EnumToString((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE))) + ", "
                 + Str("so_mode") + ": " + Str(so_mode) + ", "
                 + Str("so_call") + ": " + Num(AccountInfoDouble(ACCOUNT_MARGIN_SO_CALL)) + ", "
                 + Str("so_so") + ": " + Num(AccountInfoDouble(ACCOUNT_MARGIN_SO_SO)) + "}, "
                 + Str("symbols") + ": {" + SymbolJson(InpSymbol1) + ", " + SymbolJson(InpSymbol2) + "}}";
   int h = FileOpen(InpSettings, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("RL_ImportT1: cannot write Common\\Files\\", InpSettings, ", error ", GetLastError());
      return false;
     }
   FileWriteString(h, json + "\n");
   FileClose(h);
   PrintFormat("RL_ImportT1: account stop-out %s call %.2f so %.2f, leverage %d -> %s", so_mode,
               AccountInfoDouble(ACCOUNT_MARGIN_SO_CALL), AccountInfoDouble(ACCOUNT_MARGIN_SO_SO),
               (int)AccountInfoInteger(ACCOUNT_LEVERAGE), InpSettings);
   return true;
  }

void OnStart()
  {
   bool a = Build(InpFile1, InpSymbol1);
   bool b = Build(InpFile2, InpSymbol2);
   bool c = WriteSettings();
   PrintFormat("RL_ImportT1: done (%s %s, %s %s, settings %s)", InpSymbol1, a ? "OK" : "FAILED", InpSymbol2,
               b ? "OK" : "FAILED", c ? "OK" : "FAILED");
  }
//+------------------------------------------------------------------+
