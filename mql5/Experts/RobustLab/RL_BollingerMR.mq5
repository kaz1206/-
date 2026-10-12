//+------------------------------------------------------------------+
//| RL_BollingerMR.mq5                                               |
//| Classic Bollinger-band mean reversion, research EA for           |
//| docs/research/classics/PREREGISTRATION.md.                        |
//| On each new bar, using closed bars only (SMA InpPeriod,          |
//| +-InpDeviation standard deviations of the close):                |
//|  - flat: sell when the last close is above the upper band, buy   |
//|    when below the lower band;                                    |
//|  - exit a long when the last close is at or above the middle     |
//|    line, a short when at or below it.                            |
//| One position at most, fixed lot, NO stop loss (the classic rule; |
//| its known weakness is a trend that never comes back).            |
//| SAFETY: trades only inside the Strategy Tester (MQL_TESTER).     |
//+------------------------------------------------------------------+
#property copyright "RobustLab"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <RobustLab/Telemetry.mqh>

input int    InpPeriod    = 30;
input double InpDeviation = 2.5;
input double InpLots      = 0.10;

#define RL_BBMR_MAGIC 26101203

CTrade   g_trade;
int      g_bands = INVALID_HANDLE;
datetime g_bar   = 0;

int OnInit()
  {
   if(RL_IsFrameMode())
      return INIT_SUCCEEDED;
   if(!RL_IsTester())
     {
      Print("RL_BollingerMR: refused to start - runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   if(InpPeriod > 1 && InpDeviation > 0)
     {
      g_bands = iBands(_Symbol, _Period, InpPeriod, 0, InpDeviation, PRICE_CLOSE);
      if(g_bands == INVALID_HANDLE)
         return INIT_FAILED;
     }
   g_trade.SetExpertMagicNumber(RL_BBMR_MAGIC);
   RL_TelemetryOnInit();
   return INIT_SUCCEEDED;
  }

int PositionDir()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_BBMR_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
     }
   return 0;
  }

void CloseAll()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_BBMR_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         g_trade.PositionClose(tk);
     }
  }

void OnTick()
  {
   if(!RL_IsTester())
      return;
   RL_TelemetryOnTick();
   if(g_bands == INVALID_HANDLE || InpLots <= 0)
      return;   // not tradable: the pass runs without trading
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_bar)
      return;
   g_bar = bar;
   double mid[1], up[1], lo[1];
   if(CopyBuffer(g_bands, 0, 1, 1, mid) != 1 || CopyBuffer(g_bands, 1, 1, 1, up) != 1 ||
      CopyBuffer(g_bands, 2, 1, 1, lo) != 1)
      return;
   double c1 = iClose(_Symbol, _Period, 1);
   if(c1 <= 0)
      return;
   int pos = PositionDir();
   if(pos == 1 && c1 >= mid[0])
     {
      CloseAll();
      pos = 0;
     }
   else if(pos == -1 && c1 <= mid[0])
     {
      CloseAll();
      pos = 0;
     }
   if(pos == 0)
     {
      if(c1 > up[0])
         g_trade.Sell(InpLots, _Symbol);
      else if(c1 < lo[0])
         g_trade.Buy(InpLots, _Symbol);
     }
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
   if(g_bands != INVALID_HANDLE)
      IndicatorRelease(g_bands);
  }
