//+------------------------------------------------------------------+
//| RL_Verify_P1.mq5                                                 |
//| P1 hardware verification EA ONLY. Not part of the P1 product.    |
//| It does not trade with any intent; it opens/closes the minimum   |
//| volume deterministically so that deals exist for checking W8.    |
//| SAFETY: it refuses to start outside the Strategy Tester          |
//| (MQL_TESTER check in OnInit, re-checked before every order).     |
//|                                                                  |
//| Outputs (both FILE_COMMON and the local sandbox):                |
//|   RLV_<tag>_inputs.json  received input values        (W3)       |
//|   RLV_<tag>_env.json     terminal/account/symbol/time (W7,W9,W12)|
//|   RLV_<tag>_deals.csv    all deals from OnDeinit      (W8)       |
//|   RLV_<tag>_stats.json   TesterStatistics in OnTester/OnDeinit   |
//|   RLV_<tag>_done.json    completion marker, written last         |
//| Local sandbox only:                                              |
//|   RLV_<tag>_redact.txt   login/name used by the script to redact |
//|                          them from results (never uploaded)      |
//+------------------------------------------------------------------+
#property copyright "RobustLab verification"
#property version   "1.00"

#include <Trade\Trade.mqh>

input int             InpInt    = 1;
input double          InpDouble = 0.5;
input string          InpString = "default";
input bool            InpBool   = false;
input ENUM_TIMEFRAMES InpEnum   = PERIOD_H1;
input datetime        InpDate   = D'2000.01.01 00:00';
input string          RL_RunTag = "none";

#define RLV_MAGIC 20261008

CTrade   g_trade;
long     g_ticks          = 0;
datetime g_first_tick     = 0;
datetime g_last_tick      = 0;
long     g_first_tick_msc = 0;
long     g_last_tick_msc  = 0;
datetime g_first_tick_gmt = 0;
datetime g_last_bar       = 0;
int      g_bars_seen      = 0;
double   g_init_balance   = 0.0;

int      g_seq            = 0;
int      g_seq_oninit     = -1;
int      g_seq_ontester   = -1;
int      g_seq_ondeinit   = -1;

int      g_stat_ids[]   = {STAT_INITIAL_DEPOSIT, STAT_PROFIT, STAT_GROSS_PROFIT, STAT_GROSS_LOSS,
                           STAT_TRADES, STAT_DEALS, STAT_BALANCE_DD, STAT_EQUITY_DD};
string   g_stat_names[] = {"initial_deposit", "profit", "gross_profit", "gross_loss",
                           "trades", "deals", "balance_dd", "equity_dd"};
double   g_stat_ontester[8];
bool     g_ontester_called = false;

//--- helpers --------------------------------------------------------
string JStr(string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\r", "\\r");
   StringReplace(r, "\n", "\\n");
   return "\"" + r + "\"";
  }

string JBool(bool b) { return b ? "true" : "false"; }

string JNum(double v, int digits) { return DoubleToString(v, digits); }

string KV(string key, string json_value) { return JStr(key) + ":" + json_value; }

string TS(datetime t) { return JStr(TimeToString(t, TIME_DATE | TIME_SECONDS)); }

string Tag() { return RL_RunTag; }

// Writes UTF-8 text. Returns true on success.
bool WriteOne(string name, string text, bool common)
  {
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   if(common)
      flags |= FILE_COMMON;
   ResetLastError();
   int h = FileOpen(name, flags, '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("RLV: FileOpen failed name=%s common=%d err=%d", name, (int)common, GetLastError());
      return false;
     }
   FileWriteString(h, text);
   FileClose(h);
   return true;
  }

// Writes to both FILE_COMMON and the local sandbox; returns "c<0|1>l<0|1>".
string WriteBoth(string name, string text)
  {
   bool c = WriteOne(name, text, true);
   bool l = WriteOne(name, text, false);
   return StringFormat("c%dl%d", (int)c, (int)l);
  }

//--- safety: this EA must never send orders outside the Strategy Tester --
// MQL_TESTER is true only when the program runs in the Strategy Tester
// (single test, visual test, or optimization). On a live/demo chart it is
// false, so OnInit fails and the terminal unloads the EA before any tick.
// IsTesterOnly() is checked again right before every order call
// (defense in depth), and OnDeinit writes nothing outside the tester.
bool IsTesterOnly() { return (bool)MQLInfoInteger(MQL_TESTER); }

//--- events ---------------------------------------------------------
int OnInit()
  {
   if(!IsTesterOnly())
     {
      Print("RLV: refused to start - this verification EA runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   g_seq_oninit   = g_seq++;
   g_init_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_trade.SetExpertMagicNumber(RLV_MAGIC);
   PrintFormat("RLV: OnInit tag=%s", Tag());
   return INIT_SUCCEEDED;
  }

void OnNewBar()
  {
   if(!IsTesterOnly())   // the ONLY function that sends orders; guarded again
      return;
   double vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   // close own positions held for >= 5 bars
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != RLV_MAGIC)
         continue;
      datetime ot = (datetime)PositionGetInteger(POSITION_TIME);
      if(g_last_bar - ot >= 5 * PeriodSeconds(_Period))
         g_trade.PositionClose(tk);
     }
   // open on every 3rd bar, alternating buy/sell
   if(g_bars_seen % 3 == 0)
     {
      if((g_bars_seen / 3) % 2 == 0)
         g_trade.Buy(vol, _Symbol);
      else
         g_trade.Sell(vol, _Symbol);
     }
  }

void OnTick()
  {
   if(!IsTesterOnly())
      return;
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t))
      return;
   g_ticks++;
   if(g_first_tick == 0)
     {
      g_first_tick     = t.time;
      g_first_tick_msc = t.time_msc;
      g_first_tick_gmt = TimeGMT();
     }
   g_last_tick     = t.time;
   g_last_tick_msc = t.time_msc;

   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar != g_last_bar)
     {
      g_last_bar = bar;
      g_bars_seen++;
      OnNewBar();
     }
  }

double OnTester()
  {
   g_seq_ontester    = g_seq++;
   g_ontester_called = true;
   for(int i = 0; i < ArraySize(g_stat_ids); i++)
      g_stat_ontester[i] = TesterStatistics((ENUM_STATISTICS)g_stat_ids[i]);
   return 0.0;
  }

void OnDeinit(const int reason)
  {
   if(!IsTesterOnly())   // also called after INIT_FAILED; write nothing outside the tester
      return;
   g_seq_ondeinit = g_seq++;
   string tag = Tag();
   int open_positions_at_deinit = PositionsTotal();

   //--- redaction helper (local sandbox only, never uploaded)
   WriteOne("RLV_" + tag + "_redact.txt",
            IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "\n" + AccountInfoString(ACCOUNT_NAME) + "\n",
            false);

   //--- W3: received inputs
   string inputs = "{"
                   + KV("InpInt", IntegerToString(InpInt)) + ","
                   + KV("InpDouble", JNum(InpDouble, 15)) + ","
                   + KV("InpString", JStr(InpString)) + ","
                   + KV("InpString_len", IntegerToString(StringLen(InpString))) + ","
                   + KV("InpBool", JBool(InpBool)) + ","
                   + KV("InpEnum", IntegerToString((int)InpEnum)) + ","
                   + KV("InpDate_int", IntegerToString((long)InpDate)) + ","
                   + KV("InpDate_str", TS(InpDate)) + ","
                   + KV("RL_RunTag", JStr(RL_RunTag))
                   + "}\n";
   string w_inputs = WriteBoth("RLV_" + tag + "_inputs.json", inputs);

   //--- W8: all deals, read in OnDeinit
   bool hist_ok = HistorySelect(0, D'2100.01.01 00:00');
   int  n_deals = HistoryDealsTotal();
   string csv = "ticket,time,time_msc,type,entry,volume,price,profit,commission,swap,fee,symbol,magic,position_id,reason,comment\n";
   double sum_trade = 0.0;
   int    n_trade_deals = 0;
   for(int i = 0; i < n_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      long   type   = HistoryDealGetInteger(d, DEAL_TYPE);
      double profit = HistoryDealGetDouble(d, DEAL_PROFIT);
      double comm   = HistoryDealGetDouble(d, DEAL_COMMISSION);
      double swap   = HistoryDealGetDouble(d, DEAL_SWAP);
      double fee    = HistoryDealGetDouble(d, DEAL_FEE);
      string sym    = HistoryDealGetString(d, DEAL_SYMBOL);
      int    dg     = (int)SymbolInfoInteger(sym == "" ? _Symbol : sym, SYMBOL_DIGITS);
      string cmt    = HistoryDealGetString(d, DEAL_COMMENT);
      StringReplace(cmt, "\"", "\"\"");
      csv += (string)d + ","
             + TimeToString((datetime)HistoryDealGetInteger(d, DEAL_TIME), TIME_DATE | TIME_SECONDS) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_TIME_MSC)) + ","
             + IntegerToString(type) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_ENTRY)) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_VOLUME), 2) + ","
             + DoubleToString(HistoryDealGetDouble(d, DEAL_PRICE), dg) + ","
             + DoubleToString(profit, 2) + ","
             + DoubleToString(comm, 2) + ","
             + DoubleToString(swap, 2) + ","
             + DoubleToString(fee, 2) + ","
             + sym + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_MAGIC)) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_POSITION_ID)) + ","
             + IntegerToString(HistoryDealGetInteger(d, DEAL_REASON)) + ","
             + "\"" + cmt + "\"\n";
      if(type == DEAL_TYPE_BUY || type == DEAL_TYPE_SELL)
        {
         sum_trade += profit + comm + swap + fee;
         n_trade_deals++;
        }
     }
   string w_deals = WriteBoth("RLV_" + tag + "_deals.csv", csv);

   //--- W8: TesterStatistics in OnTester vs OnDeinit
   string st_t = "{";
   string st_d = "{";
   for(int i = 0; i < ArraySize(g_stat_ids); i++)
     {
      string sep = (i == 0) ? "" : ",";
      st_t += sep + KV(g_stat_names[i], JNum(g_stat_ontester[i], 2));
      st_d += sep + KV(g_stat_names[i], JNum(TesterStatistics((ENUM_STATISTICS)g_stat_ids[i]), 2));
     }
   st_t += "}";
   st_d += "}";
   string stats = "{"
                  + KV("ontester_called", JBool(g_ontester_called)) + ","
                  + KV("ontester", st_t) + ","
                  + KV("ondeinit", st_d) + ","
                  + KV("sum_trade_deals_profit_comm_swap_fee", JNum(sum_trade, 2))
                  + "}\n";
   string w_stats = WriteBoth("RLV_" + tag + "_stats.json", stats);

   //--- W7/W9/W12: environment as seen inside the tester
   string sym_json = "{"
                     + KV("name", JStr(_Symbol)) + ","
                     + KV("digits", IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_DIGITS))) + ","
                     + KV("point", JNum(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 10)) + ","
                     + KV("contract_size", JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), 4)) + ","
                     + KV("tick_value", JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), 10)) + ","
                     + KV("tick_size", JNum(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), 10)) + ","
                     + KV("spread", IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD))) + ","
                     + KV("spread_float", JBool((bool)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD_FLOAT))) + ","
                     + KV("swap_long", JNum(SymbolInfoDouble(_Symbol, SYMBOL_SWAP_LONG), 4)) + ","
                     + KV("swap_short", JNum(SymbolInfoDouble(_Symbol, SYMBOL_SWAP_SHORT), 4)) + ","
                     + KV("volume_min", JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 4)) + ","
                     + KV("volume_step", JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), 4)) + ","
                     + KV("volume_max", JNum(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), 4)) + ","
                     + KV("currency_profit", JStr(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT))) + ","
                     + KV("currency_margin", JStr(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_MARGIN)))
                     + "}";
   string env = "{"
                + KV("terminal_build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + ","
                + KV("terminal_path", JStr(TerminalInfoString(TERMINAL_PATH))) + ","
                + KV("terminal_data_path", JStr(TerminalInfoString(TERMINAL_DATA_PATH))) + ","
                + KV("terminal_commondata_path", JStr(TerminalInfoString(TERMINAL_COMMONDATA_PATH))) + ","
                + KV("mql_tester", JBool((bool)MQLInfoInteger(MQL_TESTER))) + ","
                + KV("mql_optimization", JBool((bool)MQLInfoInteger(MQL_OPTIMIZATION))) + ","
                + KV("mql_visual", JBool((bool)MQLInfoInteger(MQL_VISUAL_MODE))) + ","
                + KV("account_server", JStr(AccountInfoString(ACCOUNT_SERVER))) + ","
                + KV("account_company", JStr(AccountInfoString(ACCOUNT_COMPANY))) + ","
                + KV("account_currency", JStr(AccountInfoString(ACCOUNT_CURRENCY))) + ","
                + KV("account_leverage", IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE))) + ","
                + KV("account_margin_mode", IntegerToString(AccountInfoInteger(ACCOUNT_MARGIN_MODE))) + ","
                + KV("initial_balance_oninit", JNum(g_init_balance, 2)) + ","
                + KV("period", IntegerToString((int)_Period)) + ","
                + KV("period_name", JStr(EnumToString(_Period))) + ","
                + KV("symbol", sym_json) + ","
                + KV("ticks", IntegerToString(g_ticks)) + ","
                + KV("bars_seen", IntegerToString(g_bars_seen)) + ","
                + KV("first_tick", TS(g_first_tick)) + ","
                + KV("first_tick_msc", IntegerToString(g_first_tick_msc)) + ","
                + KV("first_tick_timegmt", TS(g_first_tick_gmt)) + ","
                + KV("last_tick", TS(g_last_tick)) + ","
                + KV("last_tick_msc", IntegerToString(g_last_tick_msc)) + ","
                + KV("timecurrent_at_deinit", TS(TimeCurrent())) + ","
                + KV("uninit_reason", IntegerToString(reason)) + ","
                + KV("open_positions_at_deinit", IntegerToString(open_positions_at_deinit)) + ","
                + KV("seq_oninit", IntegerToString(g_seq_oninit)) + ","
                + KV("seq_ontester", IntegerToString(g_seq_ontester)) + ","
                + KV("seq_ondeinit", IntegerToString(g_seq_ondeinit))
                + "}\n";
   string w_env = WriteBoth("RLV_" + tag + "_env.json", env);

   //--- completion marker (last)
   string done = "{"
                 + KV("tag", JStr(tag)) + ","
                 + KV("history_select_ok", JBool(hist_ok)) + ","
                 + KV("deals_total", IntegerToString(n_deals)) + ","
                 + KV("trade_deals", IntegerToString(n_trade_deals)) + ","
                 + KV("write_inputs", JStr(w_inputs)) + ","
                 + KV("write_deals", JStr(w_deals)) + ","
                 + KV("write_stats", JStr(w_stats)) + ","
                 + KV("write_env", JStr(w_env))
                 + "}\n";
   WriteBoth("RLV_" + tag + "_done.json", done);
   PrintFormat("RLV: OnDeinit done tag=%s deals=%d reason=%d", tag, n_deals, reason);
  }
//+------------------------------------------------------------------+
