//+------------------------------------------------------------------+
//| RL_TSMom.mq5                                                     |
//| Classic time-series momentum, research EA for                    |
//| docs/research/classics/PREREGISTRATION.md.                        |
//| Every InpHoldBars new bars, using closed bars only: hold a long  |
//| if the last close is above the close InpLookback bars before it, |
//| a short if below (no change when equal). Rebalances only on      |
//| those bars. One position at most, fixed lot, no SL / TP.         |
//| SAFETY: trades only inside the Strategy Tester (MQL_TESTER).     |
//+------------------------------------------------------------------+
#property copyright "RobustLab"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <RobustLab/Telemetry.mqh>

input int    InpLookback = 90;
input int    InpHoldBars = 18;
input double InpLots     = 0.10;

#define RL_TSMOM_MAGIC 26101202

CTrade   g_trade;
datetime g_bar   = 0;
long     g_count = 0;   // new bars seen since the start of the test

int OnInit()
  {
   if(RL_IsFrameMode())
      return INIT_SUCCEEDED;
   if(!RL_IsTester())
     {
      Print("RL_TSMom: refused to start - runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   g_trade.SetExpertMagicNumber(RL_TSMOM_MAGIC);
   RL_TelemetryOnInit();
   return INIT_SUCCEEDED;
  }

int PositionDir()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_TSMOM_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
     }
   return 0;
  }

void CloseAll()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_TSMOM_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         g_trade.PositionClose(tk);
     }
  }

void OnTick()
  {
   if(!RL_IsTester())
      return;
   RL_TelemetryOnTick();
   if(InpLookback <= 0 || InpHoldBars <= 0 || InpLots <= 0)
      return;   // not tradable: the pass runs without trading
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_bar)
      return;
   g_bar = bar;
   g_count++;
   if(Bars(_Symbol, _Period) < InpLookback + 2)
      return;
   if((g_count - 1) % InpHoldBars != 0)
      return;
   double c1 = iClose(_Symbol, _Period, 1);
   double c0 = iClose(_Symbol, _Period, 1 + InpLookback);
   if(c1 <= 0 || c0 <= 0)
      return;
   int target = c1 > c0 ? 1 : (c1 < c0 ? -1 : 0);
   if(target == 0)
      return;
   int pos = PositionDir();
   if(pos == target)
      return;
   if(pos != 0)
      CloseAll();
   if(target == 1)
      g_trade.Buy(InpLots, _Symbol);
   else
      g_trade.Sell(InpLots, _Symbol);
  }

double OnTester()
  {
   RL_TelemetryOnTester();
   return 0.0;
  }

void OnTesterInit()   { RL_TelemetryOnTesterInit(); }
void OnTesterPass()   { RL_TelemetryOnTesterPass(); }
void OnTesterDeinit() { RL_TelemetryOnTesterDeinit(); }

void OnDeinit(const int reason)
  {
   RL_TelemetryOnDeinit(reason);
  }
