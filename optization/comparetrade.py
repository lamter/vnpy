# coding:utf-8
import os
import logging

logging.basicConfig(level=logging.INFO)
from itertools import chain
import arrow

from myplot.kline import *
from optization.drawbacktestingtrade import DrawBacktestingTrade
from optization.drawtrade import DrawTrade


b = arrow.now()

PERIOD = '30T'
originTrlList = []

# ################################
# # 实盘盘成交
# # startTradingDay=arrow.get('2019-01-10 00:00:00+08').datetime
# drm = DrawTrade('drawtrade_realmoney.ini', )
# originTrlList.append(drm)
# drm.loadTrade()
# drm.filterTrade()
# drm.loadBar()
# # drm.draw(PERIOD, 2000, 1000)
# drm.draw(PERIOD)
# ################################

###############################
# 运行回测，生成成交图
try:
    startTradingDay = drm.matcher.startTradingDay  # 取实盘的第一笔成交开始做对比
    endTradingDay = None
except NameError:
    startTradingDay = None
    endTradingDay = None
    # startTradingDay = arrow.get('2015-11-01 00:00:00+08').datetime
    # startTradingDay = arrow.get('2018-11-14 00:00:00+08').datetime
    # endTradingDay = arrow.get('2017-03-17 00:00:00+08').datetime

dbt = DrawBacktestingTrade('drawtrade_backtesting.ini', startTradingDay=startTradingDay, endTradingDay=endTradingDay)
originTrlList.append(dbt)

# dbt.clearCollection()  # 清空数据库
# dbt.runArg()  # 生成参数
# dbt.runBacktesting()  # 批量回测
# e = arrow.now()
# print(u'运行 {} -> {} 耗时 {}'.format(b, e, e - b))
# import os
# costTime = e-b
# os.system('say "批量回测完成 耗时 {}"'.format(round(costTime.total_seconds() / 3600, 1)))

# optsv = 'rb,"BIG":False,"UNITS":2,"barXmin":84'
# dbt.config.set('DrawBacktestingTrade', 'optsv', optsv)
# dbt.config.set('DrawBacktestingTrade', 'underlyingSymbol', optsv.split(',')[0])
# # dbt.btresult = 'btresult_rb_ClassicalTurtleDonchian'

dbt.loadTrade()   # 加载成交单
# dbt.loadIndLine()   # 加载技术指标
# dbt.loadBar()# 加载数据并绘制成交图
# dbt.draw(PERIOD)

###############################

################################
# 模拟盘成交
try:
    startTradingDay = drm.matcher.startTradingDay # 取实盘的第一笔成交开始做对比
    # startTradingDay = arrow.get('2016-11-14 00:00:00+08').datetime
except NameError:
    startTradingDay = None
dsim = DrawTrade('drawtrade_sim.ini', endTradingDay =startTradingDay)
originTrlList.append(dsim)
dsim.loadTrade()
dsim.filterTrade()
dsim.loadBar()
# dsim.draw(PERIOD, 2000, 1000)
# dsim.draw(PERIOD, )
################################

originTrl = list(chain(
    *[d.originTrl for d in originTrlList]

))
# # 合并绘制成交图
tradeOnKlinePlot = tradeOnKLine(
    PERIOD, dsim.bars, originTrl, []
)
tradeOnKlinePlot.render(u'/Users/lamter/Downloads/叠加成交图.html')
