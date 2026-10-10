//+------------------------------------------------------------------+
//| RobustLab Telemetry (P1: end-of-test output only)                |
//|                                                                  |
//| Usage in an EA:                                                  |
//|   #include <RobustLab/Telemetry.mqh>                             |
//|   OnInit   -> RL_TelemetryOnInit();                              |
//|   OnTick   -> RL_TelemetryOnTick();   (first line)               |
//|   OnDeinit -> RL_TelemetryOnDeinit(reason);                      |
//|                                                                  |
//| Writes UTF-8 files to FILE_COMMON (verified on hardware, W7):    |
//|   RL_<job>_deals.csv  all deals via HistorySelect (W8)           |
//|   RL_<job>_stats.json TesterStatistics in OnDeinit (W8)          |
//|   RL_<job>_env.json   terminal/account/symbol as seen by tester, |
//|                       plus per-tick tracking (v2)                |
//|   RL_<job>_daily.csv  one row per server day (v2)                |
//|   RL_<job>_done.json  completion marker, written LAST and only   |
//|                       if every other file was written            |
//| Nothing is written outside the Strategy Tester or without a job. |
//+------------------------------------------------------------------+
#ifndef ROBUSTLAB_TELEMETRY_MQH
#define ROBUSTLAB_TELEMETRY_MQH

#define RL_TELEMETRY_VERSION "2"

input string RL_JobId = "";   // set by RobustLab; harness input, not a strategy parameter

datetime rl_first_tick     = 0;
datetime rl_last_tick      = 0;
long     rl_first_tick_msc = 0;
long     rl_last_tick_msc  = 0;
long     rl_ticks          = 0;
int      rl_bars           = 0;
datetime rl_last_bar       = 0;
double   rl_init_balance   = 0.0;

//--- v2: per-tick tracking (equity drawdown, floating loss, exposure)
double   rl_eq_peak        = 0.0;
double   rl_eq_min         = 0.0;
double   rl_eq_max_dd      = 0.0;
double   rl_eq_max_dd_pct  = 0.0;
double   rl_max_floating   = 0.0;   // most negative (equity - balance)
int      rl_max_positions  = 0;
double   rl_max_lots       = 0.0;
double   rl_min_margin     = -1.0;  // -1: never had a position
//--- v2: daily snapshot of the current server day
datetime rl_day            = 0;
double   rl_day_eq_min     = 0.0;
double   rl_day_eq_max     = 0.0;
double   rl_day_eq_close   = 0.0;
double   rl_day_bal_close  = 0.0;
int      rl_day_pos_max    = 0;
long     rl_day_ticks      = 0;
string   rl_daily_csv      = "date,balance_close,equity_close,equity_min,equity_max,positions_max,ticks\n";

int    rl_stat_ids[]   = {STAT_INITIAL_DEPOSIT, STAT_PROFIT, STAT_GROSS_PROFIT, STAT_GROSS_LOSS,
                          STAT_TRADES, STAT_DEALS, STAT_BALANCE_DD, STAT_EQUITY_DD,
                          STAT_PROFIT_FACTOR, STAT_EXPECTED_PAYOFF, STAT_RECOVERY_FACTOR,
                          STAT_SHARPE_RATIO, STAT_PROFIT_TRADES, STAT_LOSS_TRADES,
                          STAT_MAX_CONWINS, STAT_MAX_CONPROFIT_TRADES, STAT_MAX_CONLOSSES,
                          STAT_MAX_CONLOSS_TRADES};
string rl_stat_names[] = {"initial_deposit", "profit", "gross_profit", "gross_loss",
                          "trades", "deals", "balance_dd", "equity_dd",
                          "profit_factor", "expected_payoff", "recovery_factor",
                          "sharpe_ratio", "profit_trades", "loss_trades",
                          "max_conwins", "max_conprofit_trades", "max_conlosses",
                          "max_conloss_trades"};

bool RL_IsTester() { return (bool)MQLInfoInteger(MQL_TESTER); }

string RL_JStr(string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\r", "\\r");
   StringReplace(r, "\n", "\\n");
   return "\"" + r + "\"";
  }
string RL_JBool(bool b) { return b ? "true" : "false"; }
string RL_JNum(double v, int digits) { return DoubleToString(v, digits); }
string RL_KV(string key, string json_value) { return RL_JStr(key) + ":" + json_value; }
string RL_TS(datetime t) { return RL_JStr(TimeToString(t, TIME_DATE | TIME_SECONDS)); }

bool RL_Write(string name, string text)
  {
   ResetLastError();
   int h = FileOpen(name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("RL telemetry: FileOpen failed name=%s err=%d", name, GetLastError());
      return false;
     }
   uint written = FileWriteString(h, text);
   FileClose(h);
   return (written > 0 || StringLen(text) == 0);
  }

void RL_TelemetryOnInit()
  {
   rl_init_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   rl_eq_peak      = rl_init_balance;
   rl_eq_min       = rl_init_balance;
  }

void RL_FlushDay()
  {
   if(rl_day == 0)
      return;
   rl_daily_csv += TimeToString(rl_day, TIME_DATE) + ","
                   + DoubleToString(rl_day_bal_close, 2) + ","
                   + DoubleToString(rl_day_eq_close, 2) + ","
                   + DoubleToString(rl_day_eq_min, 2) + ","
                   + DoubleToString(rl_day_eq_max, 2) + ","
                   + IntegerToString(rl_day_pos_max) + ","
                   + IntegerToString(rl_day_ticks) + "\n";
  }

void RL_Track(datetime now)
  {
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   int    npos    = PositionsTotal();
   double lots    = 0.0;
   for(int i = 0; i < npos; i++)
     {
      if(PositionGetTicket(i) != 0)
         lots += PositionGetDouble(POSITION_VOLUME);
     }
   //--- equity drawdown from the running peak
   if(equity > rl_eq_peak)
      rl_eq_peak = equity;
   if(equity < rl_eq_min)
      rl_eq_min = equity;
   double dd = rl_eq_peak - equity;
   if(dd > rl_eq_max_dd)
     {
      rl_eq_max_dd     = dd;
      rl_eq_max_dd_pct = (rl_eq_peak > 0 ? dd / rl_eq_peak * 100.0 : 0.0);
     }
   if(equity - balance < rl_max_floating)
      rl_max_floating = equity - balance;
   if(npos > rl_max_positions)
      rl_max_positions = npos;
   if(lots > rl_max_lots)
      rl_max_lots = lots;
   if(npos > 0)
     {
      double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      if(ml > 0 && (rl_min_margin < 0 || ml < rl_min_margin))
         rl_min_margin = ml;
     }
   //--- daily snapshot (server day of the tick)
   datetime day = (datetime)((long)now - ((long)now % 86400));
   if(day != rl_day)
     {
      RL_FlushDay();
      rl_day         = day;
      rl_day_eq_min  = equity;
      rl_day_eq_max  = equity;
      rl_day_pos_max = 0;
      rl_day_ticks   = 0;
     }
   if(equity < rl_day_eq_min)
      rl_day_eq_min = equity;
   if(equity > rl_day_eq_max)
      rl_day_eq_max = equity;
   if(npos > rl_day_pos_max)
      rl_day_pos_max = npos;
   rl_day_eq_close  = equity;
   rl_day_bal_close = balance;
   rl_day_ticks++;
  }

void RL_TelemetryOnTick()
  {
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t))
      return;
   rl_ticks++;
   if(rl_first_tick == 0)
     {
      rl_first_tick     = t.time;
      rl_first_tick_msc = t.time_msc;
     }
   rl_last_tick     = t.time;
   rl_last_tick_msc = t.time_msc;
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar != rl_last_bar)
     {
      rl_last_bar = bar;
      rl_bars++;
     }
   RL_Track(t.time);
  }

void RL_TelemetryOnDeinit(const int reason)
  {
   if(!RL_IsTester())
      return;
   if(RL_JobId == "")
     {
      Print("RL telemetry: RL_JobId is empty, nothing written");
      return;
     }
   string prefix = "RL_" + RL_JobId + "_";

   //--- deals (no magic filter: end-of-test closes have magic 0, F4)
   bool   hist_ok = HistorySelect(0, D'2100.01.01 00:00');
   int    n_deals = HistoryDealsTotal();
   int    n_trade = 0;
   int    n_so    = 0;
   string csv = "ticket,time,time_msc,type,entry,volume,price,profit,commission,swap,fee,symbol,magic,position_id,reason,comment\n";
   for(int i = 0; i < n_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      long   type = HistoryDealGetInteger(d, DEAL_TYPE);
      string sym  = HistoryDealGetString(d, DEAL_SYMBOL);
      int    dg   = (int)SymbolInfoInteger(sym == "" ? _Symbol : sym, SYMBOL_DIGITS);
      string cmt  = HistoryDealGetString(d, DEAL_COMMENT);
      StringReplace(cmt, "\"", "\"\"");
      csv += (string)d + ","
             + TimeToString((datetime)HistoryDealGetInteger(d, DEAL_TIME), TIME_DATE | TIME_SECONDS) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_TIME_MSC)) + ","
             + IntegerToString(type) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_ENTRY)) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_VOLUME), 2) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_PRICE), dg) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_PROFIT), 2) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_COMMISSION), 2) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_SWAP), 2) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_FEE), 2) + ","
             + sym + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_MAGIC)) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_POSITION_ID)) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_REASON)) + ","
             + "\"" + cmt + "\"\n";
      if(type == DEAL_TYPE_BUY || type == DEAL_TYPE_SELL)
         n_trade++;
      if(HistoryDealGetInteger(d, DEAL_REASON) == DEAL_REASON_SO)
         n_so++;
     }
   bool ok_deals = hist_ok && RL_Write(prefix + "deals.csv", csv);

   //--- TesterStatistics (valid in OnDeinit, W8)
   string sv = "{";
   for(int i = 0; i < ArraySize(rl_stat_ids); i++)
      sv += (i == 0 ? "" : ",") + RL_KV(rl_stat_names[i], RL_JNum(TesterStatistics((ENUM_STATISTICS)rl_stat_ids[i]), 2));
   sv += "}";
   bool ok_stats = RL_Write(prefix + "stats.json",
                            "{" + RL_KV("source", RL_JStr("OnDeinit")) + "," + RL_KV("values", sv) + "}\n");

   //--- environment as seen inside the tester (spread is an observation only, F5)
   string sym_json = "{"
                     + RL_KV("name", RL_JStr(_Symbol)) + ","
                     + RL_KV("digits", IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_DIGITS))) + ","
                     + RL_KV("point", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 10)) + ","
                     + RL_KV("contract_size", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), 4)) + ","
                     + RL_KV("tick_value", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), 10)) + ","
                     + RL_KV("tick_size", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), 10)) + ","
                     + RL_KV("spread_float", RL_JBool((bool)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD_FLOAT))) + ","
                     + RL_KV("swap_long", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_SWAP_LONG), 4)) + ","
                     + RL_KV("swap_short", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_SWAP_SHORT), 4)) + ","
                     + RL_KV("volume_min", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 4)) + ","
                     + RL_KV("volume_step", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), 4)) + ","
                     + RL_KV("volume_max", RL_JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), 4)) + ","
                     + RL_KV("currency_profit", RL_JStr(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT))) + ","
                     + RL_KV("currency_margin", RL_JStr(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_MARGIN)))
                     + "}";
   string env = "{"
                + RL_KV("telemetry_version", RL_JStr(RL_TELEMETRY_VERSION)) + ","
                + RL_KV("job_id", RL_JStr(RL_JobId)) + ","
                + RL_KV("terminal_build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + ","
                + RL_KV("mql_tester", RL_JBool(RL_IsTester())) + ","
                + RL_KV("account_server", RL_JStr(AccountInfoString(ACCOUNT_SERVER))) + ","
                + RL_KV("account_company", RL_JStr(AccountInfoString(ACCOUNT_COMPANY))) + ","
                + RL_KV("account_currency", RL_JStr(AccountInfoString(ACCOUNT_CURRENCY))) + ","
                + RL_KV("account_leverage", IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE))) + ","
                + RL_KV("account_margin_mode", IntegerToString(AccountInfoInteger(ACCOUNT_MARGIN_MODE))) + ","
                + RL_KV("initial_balance", RL_JNum(rl_init_balance, 2)) + ","
                + RL_KV("period", IntegerToString((int)_Period)) + ","
                + RL_KV("period_name", RL_JStr(EnumToString(_Period))) + ","
                + RL_KV("symbol", sym_json) + ","
                + RL_KV("observed", "{" + RL_KV("spread", IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD))) + "}") + ","
                + RL_KV("ticks", IntegerToString(rl_ticks)) + ","
                + RL_KV("bars", IntegerToString(rl_bars)) + ","
                + RL_KV("first_tick", RL_TS(rl_first_tick)) + ","
                + RL_KV("first_tick_msc", IntegerToString(rl_first_tick_msc)) + ","
                + RL_KV("last_tick", RL_TS(rl_last_tick)) + ","
                + RL_KV("last_tick_msc", IntegerToString(rl_last_tick_msc)) + ","
                + RL_KV("uninit_reason", IntegerToString(reason)) + ","
                + RL_KV("tracking", "{"
                        + RL_KV("equity_peak", RL_JNum(rl_eq_peak, 2)) + ","
                        + RL_KV("equity_min", RL_JNum(rl_eq_min, 2)) + ","
                        + RL_KV("equity_max_dd", RL_JNum(rl_eq_max_dd, 2)) + ","
                        + RL_KV("equity_max_dd_pct", RL_JNum(rl_eq_max_dd_pct, 4)) + ","
                        + RL_KV("max_floating_loss", RL_JNum(rl_max_floating, 2)) + ","
                        + RL_KV("max_positions", IntegerToString(rl_max_positions)) + ","
                        + RL_KV("max_lots", RL_JNum(rl_max_lots, 2)) + ","
                        + RL_KV("min_margin_level", (rl_min_margin < 0 ? "null" : RL_JNum(rl_min_margin, 2))) + ","
                        + RL_KV("stop_out_deals", IntegerToString(n_so))
                        + "}")
                + "}\n";
   bool ok_env = RL_Write(prefix + "env.json", env);

   //--- daily series: the last day closes with the final account state (after end-of-test closes)
   if(rl_day != 0)
     {
      rl_day_eq_close  = AccountInfoDouble(ACCOUNT_EQUITY);
      rl_day_bal_close = AccountInfoDouble(ACCOUNT_BALANCE);
      if(rl_day_eq_close < rl_day_eq_min)
         rl_day_eq_min = rl_day_eq_close;
      if(rl_day_eq_close > rl_day_eq_max)
         rl_day_eq_max = rl_day_eq_close;
      RL_FlushDay();
      rl_day = 0;
     }
   bool ok_daily = RL_Write(prefix + "daily.csv", rl_daily_csv);

   //--- completion marker: only when everything above succeeded
   if(!(ok_deals && ok_stats && ok_env && ok_daily))
     {
      PrintFormat("RL telemetry: incomplete output (deals=%d stats=%d env=%d daily=%d), no completion marker",
                  (int)ok_deals, (int)ok_stats, (int)ok_env, (int)ok_daily);
      return;
     }
   string done = "{"
                 + RL_KV("job_id", RL_JStr(RL_JobId)) + ","
                 + RL_KV("telemetry_version", RL_JStr(RL_TELEMETRY_VERSION)) + ","
                 + RL_KV("deals_total", IntegerToString(n_deals)) + ","
                 + RL_KV("trade_deals", IntegerToString(n_trade))
                 + "}\n";
   if(RL_Write(prefix + "done.json", done))
      PrintFormat("RL telemetry: written job=%s deals=%d", RL_JobId, n_deals);
  }

#endif // ROBUSTLAB_TELEMETRY_MQH
