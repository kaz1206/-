//+------------------------------------------------------------------+
//| RL_Donchian.mq5                                                  |
//| Classic channel breakout (Donchian / Turtle style), research EA  |
//| for docs/research/classics/PREREGISTRATION.md.                    |
//| On each new bar, using closed bars only:                         |
//|  - enter long when the last close is above the highest high of   |
//|    the InpEntryPeriod bars before it, short when below the lowest |
//|    low; exit a long when the last close is below the lowest low  |
//|    of the InpExitPeriod bars before it (short: above the highest |
//|    high); an opposite entry signal also closes and reverses.     |
//| One position at most, fixed lot, no stop loss / take profit.     |
//| SAFETY: trades only inside the Strategy Tester (MQL_TESTER).     |
//+------------------------------------------------------------------+
#property copyright "RobustLab"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <RobustLab/Telemetry.mqh>

input int    InpEntryPeriod = 90;
input int    InpExitPeriod  = 30;
input double InpLots        = 0.10;

#define RL_DONCHIAN_MAGIC 26101201

CTrade   g_trade;
datetime g_bar = 0;

int OnInit()
  {
   if(RL_IsFrameMode())
      return INIT_SUCCEEDED;
   if(!RL_IsTester())
     {
      Print("RL_Donchian: refused to start - runs only in the Strategy Tester.");
      return INIT_FAILED;
     }
   // Never INIT_PARAMETERS_INCORRECT in an optimization (the pass would be missing); the grid has no invalid values.
   g_trade.SetExpertMagicNumber(RL_DONCHIAN_MAGIC);
   RL_TelemetryOnInit();
   return INIT_SUCCEEDED;
  }

int PositionDir()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_DONCHIAN_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
     }
   return 0;
  }

void CloseAll()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk != 0 && PositionGetInteger(POSITION_MAGIC) == RL_DONCHIAN_MAGIC && PositionGetString(POSITION_SYMBOL) == _Symbol)
         g_trade.PositionClose(tk);
     }
  }

// highest high / lowest low of `count` bars starting at bar 2 (the bars before the last closed bar)
bool Channel(int count, double &hi, double &lo)
  {
   int ih = iHighest(_Symbol, _Period, MODE_HIGH, count, 2);
   int il = iLowest(_Symbol, _Period, MODE_LOW, count, 2);
   if(ih < 0 || il < 0)
      return false;
   hi = iHigh(_Symbol, _Period, ih);
   lo = iLow(_Symbol, _Period, il);
   return hi > 0 && lo > 0;
  }

void OnTick()
  {
   if(!RL_IsTester())
      return;
   RL_TelemetryOnTick();
   if(InpEntryPeriod <= 0 || InpExitPeriod <= 0 || InpLots <= 0)
      return;   // not tradable: the pass runs without trading
   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_bar)
      return;
   g_bar = bar;
   int need = MathMax(InpEntryPeriod, InpExitPeriod) + 2;
   if(Bars(_Symbol, _Period) < need)
      return;
   double ehi, elo, xhi, xlo;
   if(!Channel(InpEntryPeriod, ehi, elo) || !Channel(InpExitPeriod, xhi, xlo))
      return;
   double c1 = iClose(_Symbol, _Period, 1);
   bool long_sig  = c1 > ehi;
   bool short_sig = c1 < elo;
   int pos = PositionDir();
   if(pos == 1 && (c1 < xlo || short_sig))
     {
      CloseAll();
      pos = 0;
     }
   else if(pos == -1 && (c1 > xhi || long_sig))
     {
      CloseAll();
      pos = 0;
     }
   if(pos == 0)
     {
      if(long_sig)
         g_trade.Buy(InpLots, _Symbol);
      else if(short_sig)
         g_trade.Sell(InpLots, _Symbol);
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
  }
