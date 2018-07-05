# encoding: UTF-8

"""
一个震荡策略，使用通道信号（如唐奇安通道，布林带等）构建一个通道。
突破大周期通道时开仓，并在小盈利时止盈，大浮亏时止损。
意在构建一个小盈多赢的震荡策略。
"""

from __future__ import division

import traceback
from collections import OrderedDict
import arrow
from threading import Timer
import tradingtime as tt

from vnpy.trader.vtConstant import *
from vnpy.trader.vtFunction import waitToContinue, exception, logDate
from vnpy.trader.vtObject import VtTradeData
from vnpy.trader.app.ctaStrategy.ctaTemplate import (BarManager, ArrayManager)
from vnpy.trader.app.ctaStrategy.svtCtaTemplate import CtaTemplate

OFFSET_CLOSE_LIST = (OFFSET_CLOSE, OFFSET_CLOSETODAY, OFFSET_CLOSEYESTERDAY)


########################################################################
class OscillationAtrChannelStrategy(CtaTemplate):
    """震荡策略"""
    className = 'OscillationAtrChannelStrategy'
    author = u'lamter'

    # 策略参数
    flinch = 2  # 连胜 flinch 次后使用轻仓
    atrNum = 14  # atr 长度
    atrChannel = 1.5  # 通道大小
    initDays = 10  # 初始化数据所用的天数
    fixedSize = 1  # 每次交易的数量
    risk = 0.05  # 要使用的风险度，根据保证金比例计算

    # 策略变量
    light = False  # 轻仓状态
    stop = None  # 风险投入
    flinchCount = 0  # 连胜次数
    atr = 0  # atr指标

    # 参数列表，保存了参数的名称
    paramList = CtaTemplate.paramList[:]
    paramList.extend([
        'flinch',
        'atrNum',
        'atrChannel',
        'initDays',
        'fixedSize',
        'risk',
    ])

    # 变量列表，保存了变量的名称
    _varList = [
        'light',
        'stop',
        'flinchCount',
        'atr',
        'hands',
    ]
    varList = CtaTemplate.varList[:]
    varList.extend(_varList)

    def __init__(self, ctaEngine, setting):
        """Constructor"""
        super(OscillationAtrChannelStrategy, self).__init__(ctaEngine, setting)

        self.hands = self.fixedSize
        self.balanceList = OrderedDict()

    def initMaxBarNum(self):
        self.maxBarNum = self.atrNum * 2

    # ----------------------------------------------------------------------
    def onInit(self):
        """初始化策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略初始化' % self.name)

        # 载入历史数据，并采用回放计算的方式初始化策略数值
        initData = self.loadBar(self.maxBarNum)

        self.log.info(u'即将加载 {} 条 bar 数据'.format(len(initData)))

        self.initContract()

        # 从数据库加载策略数据，要在加载 bar 之前。因为数据库中缓存了技术指标
        if not self.isBackTesting():
            # 需要等待保证金加载完毕
            document = self.fromDB()
            self.loadCtaDB(document)

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

        if self.stop is None:
            self.updateStop()

        self.isCloseoutVaild = True
        self.putEvent()

    # ----------------------------------------------------------------------
    @exception
    def onStart(self):
        """启动策略（必须由用户继承实现）"""
        self.log.info(u'%s策略启动' % self.name)

        if not self.isBackTesting():
            if self.xminBar and self.am and self.inited and self.trading:
                if tt.get_trading_status(self.vtSymbol) == tt.continuous_auction:
                    # 已经进入连续竞价的阶段，直接下单
                    self.log.info(u'已经处于连续竞价阶段')
                    waistSeconds = 5
                else:  # 还没进入连续竞价，使用一个定时器
                    self.log.info(u'尚未开始连续竞价')
                    moment = waitToContinue(self.vtSymbol, arrow.now().datetime)
                    wait = (moment - arrow.now().datetime)
                    waistSeconds = wait.total_seconds() - 2
                    self.log.info(u'now:{} {}后进入连续交易, 需要等待 {}'.format(arrow.now().datetime, moment, wait))

                # 提前2秒下停止单
                Timer(waistSeconds, self._orderOnStart).start()
            else:
                self.log.warning(
                    u'无法确认条件单的时机 {} {} {} {}'.format(not self.xminBar, not self.am, not self.inited, not self.trading))

            # 实盘，可以存库。
            self.saving = True

        self.putEvent()

    def _orderOnStart(self):
        """
        在onStart中的下单
        :return:
        """
        self.orderOnXminBar(self.xminBar)

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

        # 保存K线数据
        am = self.am

        am.updateBar(bar)

        if not am.inited:
            return

        # 通道内最高点
        self.atr = am.atr(self.atrNum)

        self.atrUpper = bar.close + self.atr * self.atrChannel
        self.atrDowner = bar.close - self.atr * self.atrChannel

        if self.trading:
            # self.log.warning(str(bar.datetime))
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
        if not self.trading:
            self.log.warn(u'不能下单 trading: False')
            return

        # 下单前先撤单
        self.cancelAll()

        # 计算开仓仓位
        self.updateHands()

        if self.hands == 0:
            self.log.info(u'开仓hands==0，不下单')
            return

        # 当前无仓位，发送开仓委托
        if self.pos == 0:
            self.buy(self.atrUpper, self.hands, stop=True)
            self.short(self.atrDowner, self.hands, stop=True)

        # 持有多头仓位
        elif self.pos > 0:
            self.sell(self.atrDowner, abs(self.pos), stop=True)
            self.short(self.atrDowner, self.hands, True)

        # 持有空头仓位
        elif self.pos < 0:
            self.cover(self.atrUpper, abs(self.pos), stop=True)
            self.buy(self.atrUpper, self.hands, stop=True)

    # ----------------------------------------------------------------------
    def onOrder(self, order):
        """收到委托变化推送（必须由用户继承实现）"""
        log = self.log.info
        if order.status == STATUS_REJECTED:
            log = self.log.warning
            for k, v in order.rawData.items():
                log(u'{} {}'.format(k, v))
        log(u'状态:{status} 成交:{tradedVolume}'.format(**order.__dict__))

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
            textList = [u'{}{}'.format(trade.direction, trade.offset)]
            textList.append(u'资金变化 {} -> {}'.format(originCapital, self.capital))
            textList.append(u'仓位{} -> {}'.format(self.prePos, self.pos))
            textList.append(u'手续费 {} 利润 {}'.format(round(charge, 2), round(profile, 2)))
            textList.append(
                u','.join([u'{} {}'.format(k, v) for k, v in self.positionDetail.toHtml().items()])
            )

            self.log.info(u'\n'.join(textList))
        if self.isBackTesting():
            if self.capital <= 0:
                # 回测中爆仓了
                self.capital = 0

        log = u'{} {} {} {} {} {} {} {}'.format(round(self.atr, 2), self.pos, trade.direction, trade.offset, trade.price,
                                                 trade.volume, profile, self.rtBalance)
        self.log.warning(log)

        if self.pos == 0:
            if profile > 0:
                # 盈利，轻仓
                self.flinchCount += 1
            else:
                # 重置连胜计数
                self.flinchCount = 0
                # 出现亏损后加仓
                self.updateStop()
                # 使用重仓
                self.light = False

            if self.flinchCount >= self.flinch:
                self.light = True

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
        if self.atr == 0:
            return

        # 最大开仓手数
        minHands = int(self.stop / (self.atr * self.atrChannel * 2 * self.size))

        if self.light:
            minHands = min(minHands, 1)

        self.hands = min(minHands, self.maxHands)

    @property
    def maxHands(self):
        return max(0, int(
            self.capital / (
                self.size * self.bar.close * self.marginRate)))

    def toSave(self):
        """
        将策略新增的 varList 全部存库
        :return:
        """
        dic = super(OscillationAtrChannelStrategy, self).toSave()
        # 将新增的 varList 全部存库
        dic.update({k: getattr(self, k) for k in self._varList})

        return dic

    def loadCtaDB(self, document=None):
        super(OscillationAtrChannelStrategy, self).loadCtaDB(document)
        if document:
            for k in self._varList:
                try:
                    setattr(self, k, document[k])
                except KeyError:
                    self.log.warning(u'未保存的key {}'.format(k))

    def updateStop(self):
        self.log.info(u'调整风险投入')
        self.stop = self.capital * self.risk
