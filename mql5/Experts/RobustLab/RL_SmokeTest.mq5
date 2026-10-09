//+------------------------------------------------------------------+
//| RL_SmokeTest.mq5                                                 |
//| Tiny deterministic EA used only to check the P1 pipeline         |
//| (moving-average cross, one position, fixed lot). It has no       |
//| research value.                                                  |
//| SAFETY: refuses to run outside the Strategy Tester.              |
//+------------------------------------------------------------------+
#property copyright "RobustLab"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <RobustLab/Telemetry.mqh>

input int    InpFastPeriod = 12;
input int    InpSlowPeriod = 48;
input double InpLots       = 0.01;

#define RL_SMOKE_MAGIC 26100901

CTrade   g_trade;
int      g_fast = INVALID_HANDLE;
int      g_slow = INVALID_HANDLE;
datetime g_bar  = 0;

int OnInit()
  {
   if(!RL_IsTester())
     {
      Print("RL_SmokeTest: refused to start - runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   if(InpFastPeriod <= 0 || InpSlowPeriod <= InpFastPeriod || InpLots <= 0)
      return INIT_PARAMETERS_INCORRECT;
   g_fast = iMA(_Symbol, _Period, InpFastPeriod, 0, MODE_SMA, PRICE_CLOSE);
   g_slow = iMA(_Symbol, _Period, InpSlowPeriod, 0, MODE_SMA, PRICE_CLOSE);
   if(g_fast == INVALID_HANDLE || g_slow == INVALID_HANDLE)
      return INIT_FAILED;
   g_trade.SetExpertMagicNumber(RL_SMOKE_MAGIC);
   RL_TelemetryOnInit();
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   if(!RL_IsTester())
      return;
   RL_TelemetryOnTick();

   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_bar)
      return;
   g_bar = bar;

   double f[2], s[2];
   if(CopyBuffer(g_fast, 0, 1, 2, f) != 2 || CopyBuffer(g_slow, 0, 1, 2, s) != 2)
      return;
   bool cross_up   = f[0] <= s[0] && f[1] > s[1];   // index 1 = last closed bar
   bool cross_down = f[0] >= s[0] && f[1] < s[1];
   if(!cross_up && !cross_down)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_SMOKE_MAGIC)
         g_trade.PositionClose(tk);
     }
   if(cross_up)
      g_trade.Buy(InpLots, _Symbol);
   else
      g_trade.Sell(InpLots, _Symbol);
  }

void OnDeinit(const int reason)
  {
   RL_TelemetryOnDeinit(reason);
   if(g_fast != INVALID_HANDLE)
      IndicatorRelease(g_fast);
   if(g_slow != INVALID_HANDLE)
      IndicatorRelease(g_slow);
  }
//+------------------------------------------------------------------+
