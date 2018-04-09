# encoding: UTF-8

"""
感谢Darwin Quant贡献的策略思路。
知乎专栏原文：https://zhuanlan.zhihu.com/p/24448511

策略逻辑：
1. 布林通道（信号）
2. CCI指标（过滤）
3. ATR指标（止损）

适合品种：螺纹钢
适合周期：15分钟

这里的策略是作者根据原文结合vn.py实现，对策略实现上做了一些修改，仅供参考。

"""

from __future__ import division

from collections import OrderedDict
import arrow
from threading import Timer
import tradingtime as tt

from vnpy.trader.vtConstant import *
from vnpy.trader.vtFunction import waitToContinue, exception
from vnpy.trader.vtObject import VtTradeData
from vnpy.trader.app.ctaStrategy.ctaTemplate import (BarManager, ArrayManager)
from vnpy.trader.app.ctaStrategy.svtCtaTemplate import CtaTemplate

OFFSET_CLOSE_LIST = (OFFSET_CLOSE, OFFSET_CLOSETODAY, OFFSET_CLOSEYESTERDAY)


########################################################################
class SvtBollChannelStrategy(CtaTemplate):
    """基于布林通道的交易策略"""
    className = 'SvtBollChannelStrategy'
    author = u'用Python的交易员'

    # 策略参数
    bollWindow = 18  # 布林通道窗口数
    bollDev = 3.4  # 布林通道的偏差
    cciWindow = 10  # CCI窗口数
    atrWindow = 30  # ATR窗口数
    slMultiplier = 5.2  # 计算止损距离的乘数
    initDays = 10  # 初始化数据所用的天数
    fixedSize = 1  # 每次交易的数量
    risk = slMultiplier / 100.  # 每笔风险投入

    # 策略变量
    bollUp = 0  # 布林通道上轨
    bollDown = 0  # 布林通道下轨
    cciValue = 0  # CCI指标数值
    atrValue = 0  # ATR指标数值

    intraTradeHigh = 0  # 持仓期内的最高点
    intraTradeLow = 0  # 持仓期内的最低点
    longStop = 0  # 多头止损
    shortStop = 0  # 空头止损

    # 参数列表，保存了参数的名称
    paramList = CtaTemplate.paramList[:]
    paramList.extend([
        'bollWindow',
        'bollDev',
        'cciWindow',
        'atrWindow',
        'slMultiplier',
        'initDays',
        'fixedSize',
        'risk',
    ])

    # 变量列表，保存了变量的名称
    varList = CtaTemplate.varList[:]
    _varList = [
        'bollUp',
        'bollDown',
        'cciValue',
        'atrValue',
        'intraTradeHigh',
        'intraTradeLow',
        'longStop',
        'shortStop',
        'hands'
    ]
    varList.extend(_varList)

    def __init__(self, ctaEngine, setting):
        """Constructor"""
        super(SvtBollChannelStrategy, self).__init__(ctaEngine, setting)

        self.hands = self.fixedSize
        self.balanceList = OrderedDict()
        self.a = 0

    def initMaxBarNum(self):
        self.maxBarNum = max(self.atrWindow, self.bollWindow, self.cciWindow)

    # ----------------------------------------------------------------------

    # ----------------------------------------------------------------------
    def onInit(self):
        """初始化策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略初始化' % self.name)

        # 载入历史数据，并采用回放计算的方式初始化策略数值
        initData = self.loadBar(self.maxBarNum)

        self.log.info(u'即将加载 {} 条 bar 数据'.format(len(initData)))

        self.initContract()

        for bar in initData:
            self.bm.bar = bar
            # TOOD 测试代码
            self.tradingDay = bar.tradingDay
            self.onBar(bar)
            self.bm.preBar = bar

        # self.log.info(u'加载的最后一个 bar {}'.format(bar.datetime))

        if len(initData) >= self.maxBarNum:
            self.log.info(u'初始化完成')
        else:
            self.log.info(u'初始化数据不足!')

        # 从数据库加载策略数据
        if not self.isBackTesting():
            # 需要等待保证金加载完毕
            document = self.fromDB()
            self.loadCtaDB(document)

        self.isCloseoutVaild = True

        self.putEvent()

    # ----------------------------------------------------------------------
    def onStart(self):
        """启动策略（必须由用户继承实现）"""
        self.log.info(u'%s策略启动' % self.name)

        if self.xminBar and self.am and self.inited and self.trading:
            if tt.get_trading_status(self.vtSymbol) == tt.continuous_auction:
                # 已经进入连续竞价的阶段，直接下单
                self.log.info(u'已经处于连续竞价阶段')
                self._orderOnStart()
            else:  # 还没进入连续竞价，使用一个定时器

                self.log.info(u'尚未开始连续竞价')
                moment = waitToContinue(self.vtSymbol, arrow.now().datetime)
                wait = (moment - arrow.now().datetime).total_seconds()
                self.log.info(u'{} 后开始下停止单'.format(wait))

                Timer(wait, self._orderOnStart).start()

        if not self.isBackTesting():
            # 实盘，可以存库。
            self.saving = True

        self.putEvent()

    @exception
    def _orderOnStart(self):
        """
        在onStart中的下单
        :return:
        """
        self.cancelAll()
        self.orderOnXminBar(self.barXmin)

    # ----------------------------------------------------------------------
    def onStop(self):
        """停止策略（必须由用户继承实现）"""
        self.log.info(u'%s策略停止' % self.name)
        self.putEvent()
        # self.saveDB()

    # ----------------------------------------------------------------------
    def onTick(self, tick):
        """收到行情TICK推送（必须由用户继承实现）"""
        if self.trading:
            self.bm.updateTick(tick)

    # ----------------------------------------------------------------------
    def onBar(self, bar):
        """
        self.bar 更新完最后一个 tick ，在生成新的 bar 之前将 self.bar 传入
        该函数是由下一根 bar 的第一个 tick 驱动的，而不是当前 bar 的最后一个 tick
        :param bar:
        :return:
        """
        self.bm.updateXminBar(bar)
        if self.isCloseoutVaild and self.rtBalance < 0:
            # 爆仓，一键平仓
            self.closeout()

    # ----------------------------------------------------------------------
    def onXminBar(self, xminBar):
        """
        这个函数是由 self.xminBar 的最后一根 bar 驱动的
        执行完这个函数之后，会立即更新到下一个函数
        :param xminBar:
        :return:
        """
        bar = xminBar

        # 全撤之前发出的委托
        self.cancelAll()

        # 保存K线数据
        am = self.am

        am.updateBar(bar)

        if not am.inited:
            return

        # 计算指标数值
        self.bollUp, self.bollDown = am.boll(self.bollWindow, self.bollDev)
        self.cciValue = am.cci(self.cciWindow)
        self.atrValue = am.atr(self.atrWindow)

        if self.trading:
            self.orderOnXminBar(bar)

        # 发出状态更新事件
        self.saveDB()
        self.putEvent()
        self.log.info(u'更新 XminBar {}'.format(xminBar.datetime))

    def orderOnXminBar(self, bar):
        """
        在 onXminBar 中的的指标计算和下单逻辑
        :param am:
        :param bar:
        :return:
        """

        # 判断是否要进行交易
        self.updateHands()

        # 当前无仓位，发送开仓委托
        if self.pos == 0:
            self.intraTradeHigh = bar.high
            self.intraTradeLow = bar.low

            if self.cciValue > 0:
                self.buy(self.bollUp, self.hands, True)

            elif self.cciValue < 0:
                self.short(self.bollDown, self.hands, True)

        # 持有多头仓位
        elif self.pos > 0:
            self.intraTradeHigh = max(self.intraTradeHigh, bar.high)
            self.intraTradeLow = bar.low
            self.longStop = self.intraTradeHigh - self.atrValue * self.slMultiplier

            self.sell(self.longStop, abs(self.pos), True)

        # 持有空头仓位
        elif self.pos < 0:
            self.intraTradeHigh = bar.high
            self.intraTradeLow = min(self.intraTradeLow, bar.low)
            self.shortStop = self.intraTradeLow + self.atrValue * self.slMultiplier

            self.cover(self.shortStop, abs(self.pos), True)

    # ----------------------------------------------------------------------

    def onOrder(self, order):
        """收到委托变化推送（必须由用户继承实现）"""
        dic = {}
        t = u'报单回报\n'
        t += u'ID: {}\n'.format(id(order))
        for k, v in order.__dict__.items():
            if k == 'rawData':
                continue
            dic[k] = v
            t += u'{}: {}\n'.format(k, v)

        self.log.warning(t)

        self.ctaEngine.saveOrderback(dic)

    # ----------------------------------------------------------------------
    def onTrade(self, trade):
        assert isinstance(trade, VtTradeData)

        originCapital = preCapital = self.capital

        self.charge(trade.offset, trade.price, trade.volume)

        # 手续费
        charge = preCapital - self.capital

        preCapital = self.capital

        # 回测时滑点
        if self.isBackTesting():
            self.chargeSplipage(trade.volume)

        # 计算成本价和利润
        self.capitalBalance(trade)
        profile = self.capital - preCapital

        if not self.isBackTesting():
            # if self.isBackTesting():
            textList = [u'{}{}'.format(trade.direction, trade.offset)]
            textList.append(u'资金变化 {} -> {}'.format(originCapital, self.capital))
            textList.append(u'仓位{} -> {}'.format(self.prePos, self.pos))
            textList.append(u'手续费 {} 利润 {}'.format(round(charge, 2), round(profile, 2)))
            textList.append(
                u','.join([u'{} {}'.format(k, v) for k, v in self.positionDetail.toHtml().items()])
            )

            self.log.warning(u'\n'.join(textList))
        if self.isBackTesting():
            if self.capital <= 0:
                # 回测中爆仓了
                self.capital = 0

        # 成交后重新下单

        self.orderOnXminBar(self.xminBar)

        # 发出状态更新事件
        self.saveDB()
        self.putEvent()

    # ----------------------------------------------------------------------
    def onStopOrder(self, so):
        """停止单推送"""

        self.putEvent()

    def updateHands(self):
        """
        更新开仓手数
        :return:
        """

        if self.capital <= 0:
            self.hands = 0
            return

        # 以下技术指标为0时，不更新手数
        # 在长时间封跌涨停板后，会出现以下技术指标为0的情况
        if self.slMultiplier == 0:
            return
        if self.atrValue == 0:
            return

        minHands = max(0, int(self.capital * self.risk / (self.size * self.atrValue * self.slMultiplier)))
        maxHands = max(0, int(
            self.capital * 0.95 / (
                self.size * self.bar.close * self.marginRate)))

        self.hands = min(minHands, maxHands)

    def toSave(self):
        """
        将策略新增的 varList 全部存库
        :return:
        """
        dic = super(SvtBollChannelStrategy, self).toSave()
        # 将新增的 varList 全部存库
        dic.update({k: getattr(self, k) for k in self._varList})
        return dic

    def loadCtaDB(self, document=None):
        super(SvtBollChannelStrategy, self).loadCtaDB(document)
        if document:
            for k in self._varList:
                try:
                    setattr(self, k, document[k])
                except KeyError:
                    self.log.warning(u'未保存的key {}'.format(k))
