//+------------------------------------------------------------------+
//| RL_Verify_P3.mq5                                                 |
//| P3 hardware verification EA ONLY. Not part of the product.       |
//| Checks MT5 optimization behaviour (P3_PLAN section 5, X1-X10):          |
//| full-grid optimization from an ini, Frames delivery, frame size, |
//| optimization cache, single test vs optimization pass.            |
//|                                                                  |
//| Trading logic: moving-average cross, one position, fixed lot.    |
//| It has no research value.                                        |
//|                                                                  |
//| SAFETY: orders are sent only when MQL_TESTER is true (agents).   |
//| The frame-mode instance that MT5 runs on a terminal chart during |
//| optimization (MQL_FRAME_MODE) never trades: OnTick returns       |
//| immediately and no indicator or trade object is used there. On a |
//| normal chart (neither tester nor frame mode) OnInit fails.       |
//|                                                                  |
//| Outputs (FILE_COMMON unless noted):                              |
//|  agents (one per pass)                                           |
//|   RLV_<tag>_agent_f<fast>_s<slow>.json  pass result + FrameAdd   |
//|  frame-mode instance (terminal side)                             |
//|   RLV_<tag>_fm_init.json   OnTesterInit was called (+ local)     |
//|   RLV_<tag>_frames.csv     one line per received frame           |
//|   RLV_<tag>_daily.csv      daily equity carried by the frames    |
//|   RLV_<tag>_fm_done.json   counts, written last (+ local)        |
//|   RLV_<tag>_redact.txt     login/name for redaction (local only, |
//|                            never uploaded)                       |
//+------------------------------------------------------------------+
#property copyright "RobustLab verification"
#property version   "1.00"

#include <Trade\Trade.mqh>

input int    InpFast     = 10;
input int    InpSlow     = 30;
input double InpLots     = 0.01;
input int    InpFramePad = 0;      // extra doubles appended to each frame (frame size test, X5)
input string RL_RunTag   = "none";

#define RLV_MAGIC 20261011

CTrade   g_trade;
int      g_fast_h = INVALID_HANDLE;
int      g_slow_h = INVALID_HANDLE;
datetime g_bar    = 0;

//--- daily equity (agent side)
datetime g_day      = 0;
double   g_day_eq   = 0.0;
datetime g_days[];
double   g_eqs[];

int    g_stat_ids[]   = {STAT_PROFIT, STAT_TRADES, STAT_DEALS, STAT_GROSS_PROFIT, STAT_GROSS_LOSS,
                         STAT_BALANCE_DD, STAT_EQUITY_DD, STAT_PROFIT_FACTOR, STAT_EXPECTED_PAYOFF,
                         STAT_RECOVERY_FACTOR, STAT_SHARPE_RATIO, STAT_INITIAL_DEPOSIT};
string g_stat_names[] = {"profit", "trades", "deals", "gross_profit", "gross_loss",
                         "balance_dd", "equity_dd", "profit_factor", "expected_payoff",
                         "recovery_factor", "sharpe_ratio", "initial_deposit"};

//--- frame-mode counters (terminal side)
int      g_pass_events   = 0;
int      g_frames        = 0;
int      g_frames_deinit = 0;
int      g_duplicates    = 0;
ulong    g_passes[];
datetime g_fm_started    = 0;

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

string Tag() { return RL_RunTag; }

bool IsTesterOnly() { return (bool)MQLInfoInteger(MQL_TESTER); }

bool IsFrameMode() { return (bool)MQLInfoInteger(MQL_FRAME_MODE); }

// Writes UTF-8 text (overwrites). Returns true on success.
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

// Appends UTF-8 text to a FILE_COMMON file.
bool AppendCommon(string name, string text)
  {
   ResetLastError();
   int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("RLV: append failed name=%s err=%d", name, GetLastError());
      return false;
     }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, text);
   FileClose(h);
   return true;
  }

void PushDay(datetime day, double eq)
  {
   int n = ArraySize(g_days);
   ArrayResize(g_days, n + 1);
   ArrayResize(g_eqs, n + 1);
   g_days[n] = day;
   g_eqs[n]  = eq;
  }

//--- agent side -----------------------------------------------------
int OnInit()
  {
   if(IsFrameMode())
      return INIT_SUCCEEDED;   // terminal-side collector: no indicators, no trading
   if(!IsTesterOnly())
     {
      Print("RLV: refused to start - this verification EA runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   g_fast_h = iMA(_Symbol, _Period, InpFast, 0, MODE_SMA, PRICE_CLOSE);
   g_slow_h = iMA(_Symbol, _Period, InpSlow, 0, MODE_SMA, PRICE_CLOSE);
   if(g_fast_h == INVALID_HANDLE || g_slow_h == INVALID_HANDLE)
      return INIT_FAILED;
   g_trade.SetExpertMagicNumber(RLV_MAGIC);
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   if(!IsTesterOnly())   // never trade in frame mode or on a chart
      return;

   datetime now = TimeCurrent();
   datetime day = (datetime)(((long)now / 86400) * 86400);
   if(g_day != 0 && day != g_day)
      PushDay(g_day, g_day_eq);
   g_day    = day;
   g_day_eq = AccountInfoDouble(ACCOUNT_EQUITY);

   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_bar)
      return;
   g_bar = bar;

   double f[2], s[2];
   if(CopyBuffer(g_fast_h, 0, 1, 2, f) != 2 || CopyBuffer(g_slow_h, 0, 1, 2, s) != 2)
      return;
   bool cross_up   = f[0] <= s[0] && f[1] > s[1];
   bool cross_down = f[0] >= s[0] && f[1] < s[1];
   if(!cross_up && !cross_down)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RLV_MAGIC)
         g_trade.PositionClose(tk);
     }
   if(!IsTesterOnly())
      return;
   if(cross_up)
      g_trade.Buy(InpLots, _Symbol);
   else
      g_trade.Sell(InpLots, _Symbol);
  }

double OnTester()
  {
   if(!IsTesterOnly())
      return 0.0;
   if(g_day != 0)
      PushDay(g_day, g_day_eq);

   int nst = ArraySize(g_stat_ids);
   int nd  = ArraySize(g_days);
   int pad = MathMax(InpFramePad, 0);
   double data[];
   ArrayResize(data, 3 + nst + 2 * nd + pad);
   data[0] = 1;            // frame format version
   data[1] = nst;
   data[2] = nd;
   for(int i = 0; i < nst; i++)
      data[3 + i] = TesterStatistics((ENUM_STATISTICS)g_stat_ids[i]);
   for(int k = 0; k < nd; k++)
     {
      data[3 + nst + 2 * k]     = (double)(long)g_days[k];
      data[3 + nst + 2 * k + 1] = g_eqs[k];
     }
   for(int p = 0; p < pad; p++)
      data[3 + nst + 2 * nd + p] = p;

   double profit = TesterStatistics(STAT_PROFIT);
   ResetLastError();
   bool frame_ok = FrameAdd("rlv", (long)nd, profit, data);
   int frame_err = GetLastError();

   string st = "{";
   for(int i = 0; i < nst; i++)
      st += (i == 0 ? "" : ",") + KV(g_stat_names[i], JNum(data[3 + i], 2));
   st += "}";
   string js = "{"
               + KV("tag", JStr(Tag())) + ","
               + KV("fast", IntegerToString(InpFast)) + ","
               + KV("slow", IntegerToString(InpSlow)) + ","
               + KV("frame_pad", IntegerToString(pad)) + ","
               + KV("mql_tester", JBool(IsTesterOnly())) + ","
               + KV("mql_optimization", JBool((bool)MQLInfoInteger(MQL_OPTIMIZATION))) + ","
               + KV("terminal_build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + ","
               + KV("agent_data_path", JStr(TerminalInfoString(TERMINAL_DATA_PATH))) + ","
               + KV("days", IntegerToString(nd)) + ","
               + KV("first_day", JStr(nd > 0 ? TimeToString(g_days[0], TIME_DATE) : "")) + ","
               + KV("last_day", JStr(nd > 0 ? TimeToString(g_days[nd - 1], TIME_DATE) : "")) + ","
               + KV("last_equity", JNum(nd > 0 ? g_eqs[nd - 1] : 0.0, 2)) + ","
               + KV("frame_doubles", IntegerToString(ArraySize(data))) + ","
               + KV("frame_add_ok", JBool(frame_ok)) + ","
               + KV("frame_add_err", IntegerToString(frame_err)) + ","
               + KV("stats", st)
               + "}\n";
   WriteOne(StringFormat("RLV_%s_agent_f%d_s%d.json", Tag(), InpFast, InpSlow), js, true);

   // identifiable custom criterion: shows in the XML "Result" column only if the custom criterion is used
   return InpFast * 1000.0 + InpSlow;
  }

void OnDeinit(const int reason)
  {
   if(g_fast_h != INVALID_HANDLE)
      IndicatorRelease(g_fast_h);
   if(g_slow_h != INVALID_HANDLE)
      IndicatorRelease(g_slow_h);
  }

//--- frame-mode side (terminal) -------------------------------------
void OnTesterInit()
  {
   g_fm_started = TimeLocal();
   string tag = Tag();
   WriteOne("RLV_" + tag + "_redact.txt",
            IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "\n" + AccountInfoString(ACCOUNT_NAME) + "\n",
            false);
   WriteOne("RLV_" + tag + "_frames.csv",
            "phase,pass,name,id,value,doubles,fast,slow,format,n_stats,days,profit,trades,first_day,last_day,last_equity,pass_event\n",
            true);
   WriteOne("RLV_" + tag + "_daily.csv", "pass,day,equity_close\n", true);
   string js = "{"
               + KV("tag", JStr(tag)) + ","
               + KV("mql_frame_mode", JBool(IsFrameMode())) + ","
               + KV("mql_tester", JBool(IsTesterOnly())) + ","
               + KV("mql_optimization", JBool((bool)MQLInfoInteger(MQL_OPTIMIZATION))) + ","
               + KV("terminal_build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + ","
               + KV("local_time", JStr(TimeToString(g_fm_started, TIME_DATE | TIME_SECONDS)))
               + "}\n";
   WriteBoth("RLV_" + tag + "_fm_init.json", js);
  }

string ParamValue(string &params[], uint count, string key)
  {
   string prefix = key + "=";
   for(uint i = 0; i < count; i++)
      if(StringFind(params[i], prefix) == 0)
         return StringSubstr(params[i], StringLen(prefix));
   return "";
  }

void DrainFrames(string phase)
  {
   string tag = Tag();
   ulong  pass;
   string name;
   long   id;
   double value;
   double data[];
   while(FrameNext(pass, name, id, value, data))
     {
      g_frames++;
      if(phase == "deinit")
         g_frames_deinit++;
      int n = ArraySize(g_passes);
      for(int i = 0; i < n; i++)
         if(g_passes[i] == pass)
           {
            g_duplicates++;
            break;
           }
      ArrayResize(g_passes, n + 1);
      g_passes[n] = pass;

      string params[];
      uint   pc = 0;
      string fs = "", ss = "";
      if(FrameInputs(pass, params, pc))
        {
         fs = ParamValue(params, pc, "InpFast");
         ss = ParamValue(params, pc, "InpSlow");
        }

      int    nd_ = ArraySize(data);
      int    fmt = nd_ > 0 ? (int)data[0] : -1;
      int    nst = nd_ > 1 ? (int)data[1] : 0;
      int    nd  = nd_ > 2 ? (int)data[2] : 0;
      double profit = nd_ > 3 ? data[3] : 0.0;
      double trades = nd_ > 4 ? data[4] : 0.0;
      string first_day = "", last_day = "";
      double last_eq = 0.0;
      string daily = "";
      if(nd > 0 && nd_ >= 3 + nst + 2 * nd)
        {
         first_day = TimeToString((datetime)(long)data[3 + nst], TIME_DATE);
         last_day  = TimeToString((datetime)(long)data[3 + nst + 2 * (nd - 1)], TIME_DATE);
         last_eq   = data[3 + nst + 2 * (nd - 1) + 1];
         for(int k = 0; k < nd; k++)
            daily += StringFormat("%I64u,%s,%s\n", pass,
                                  TimeToString((datetime)(long)data[3 + nst + 2 * k], TIME_DATE),
                                  DoubleToString(data[3 + nst + 2 * k + 1], 2));
        }
      string line = StringFormat("%s,%I64u,%s,%I64d,%s,%d,%s,%s,%d,%d,%d,%s,%s,%s,%s,%s,%d\n",
                                 phase, pass, name, id, DoubleToString(value, 2), nd_, fs, ss, fmt, nst, nd,
                                 DoubleToString(profit, 2), DoubleToString(trades, 0),
                                 first_day, last_day, DoubleToString(last_eq, 2), g_pass_events);
      AppendCommon("RLV_" + tag + "_frames.csv", line);
      if(daily != "")
         AppendCommon("RLV_" + tag + "_daily.csv", daily);
     }
  }

void OnTesterPass()
  {
   g_pass_events++;
   DrainFrames("pass");
  }

void OnTesterDeinit()
  {
   string tag = Tag();
   DrainFrames("deinit");

   // re-scan from the beginning: how many frames does the terminal hold in total?
   int    rescan = 0;
   ulong  pass;
   string name;
   long   id;
   double value;
   double data[];
   bool   first_ok = FrameFirst();
   while(FrameNext(pass, name, id, value, data))
      rescan++;

   string js = "{"
               + KV("tag", JStr(tag)) + ","
               + KV("pass_events", IntegerToString(g_pass_events)) + ","
               + KV("frames_received", IntegerToString(g_frames)) + ","
               + KV("frames_received_in_deinit", IntegerToString(g_frames_deinit)) + ","
               + KV("duplicate_passes", IntegerToString(g_duplicates)) + ","
               + KV("frame_first_ok", JBool(first_ok)) + ","
               + KV("frames_on_rescan", IntegerToString(rescan)) + ","
               + KV("started_local", JStr(TimeToString(g_fm_started, TIME_DATE | TIME_SECONDS))) + ","
               + KV("finished_local", JStr(TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS)))
               + "}\n";
   WriteBoth("RLV_" + tag + "_fm_done.json", js);
   PrintFormat("RLV: OnTesterDeinit tag=%s frames=%d rescan=%d", tag, g_frames, rescan);
  }
//+------------------------------------------------------------------+
