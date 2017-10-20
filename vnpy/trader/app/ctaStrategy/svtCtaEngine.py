# encoding: UTF-8

'''
本文件中实现了CTA策略引擎，针对CTA类型的策略，抽象简化了部分底层接口的功能。

关于平今和平昨规则：
1. 普通的平仓OFFSET_CLOSET等于平昨OFFSET_CLOSEYESTERDAY
2. 只有上期所的品种需要考虑平今和平昨的区别
3. 当上期所的期货有今仓时，调用Sell和Cover会使用OFFSET_CLOSETODAY，否则
   会使用OFFSET_CLOSE
4. 以上设计意味着如果Sell和Cover的数量超过今日持仓量时，会导致出错（即用户
   希望通过一个指令同时平今和平昨）
5. 采用以上设计的原因是考虑到vn.trader的用户主要是对TB、MC和金字塔类的平台
   感到功能不足的用户（即希望更高频的交易），交易策略不应该出现4中所述的情况
6. 对于想要实现4中所述情况的用户，需要实现一个策略信号引擎和交易委托引擎分开
   的定制化统结构（没错，得自己写）
'''

from __future__ import division

import time
import traceback
import datetime
from itertools import chain
from bson.codec_options import CodecOptions

from pymongo import IndexModel, ASCENDING, DESCENDING
from vnpy.event import Event
from vnpy.trader.vtEvent import *
from vnpy.trader.vtConstant import *
from vnpy.trader.vtObject import VtTickData, VtBarData
from vnpy.trader.vtGateway import VtSubscribeReq, VtOrderReq, VtCancelOrderReq, VtLogData
from vnpy.trader.vtFunction import todayDate, getJsonPath
from vnpy.trader.app.ctaStrategy.ctaEngine import CtaEngine as VtCtaEngine

from .ctaBase import *
from .strategy import STRATEGY_CLASS


########################################################################
class CtaEngine(VtCtaEngine):
    """CTA策略引擎"""

    @property
    def LOCAL_TIMEZONE(self):
        return self.mainEngine.LOCAL_TIMEZONE

    def __init__(self, mainEngine, eventEngine):
        super(CtaEngine, self).__init__(mainEngine, eventEngine)

        # 历史行情的 collection
        self.mainEngine.dbConnect()

        # 1min bar
        self.ctpCol1minBar = self.mainEngine.ctpdb[MINUTE_COL_NAME].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=self.LOCAL_TIMEZONE))

        # 日线 bar
        self.ctpCol1dayBar = self.mainEngine.ctpdb[DAY_COL_NAME].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=self.LOCAL_TIMEZONE))

        # 尝试创建 ctaCollection
        self.createCtaCollection()

        # cta 策略存库
        self.ctaCol = self.mainEngine.strategyDB[CTA_COL_NAME].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=self.LOCAL_TIMEZONE))

        if __debug__:
            import pymongo.collection
            assert isinstance(self.ctpCol1dayBar, pymongo.collection.Collection)
            assert isinstance(self.ctpCol1minBar, pymongo.collection.Collection)

    def loadBar(self, symbol, collectionName, barNum, barPeriod=1):
        """
        从数据库中读取历史行情
        :param symbol:
        :param collectionName:  bar_1min  OR bar_1day
        :param barNum: 要加载的 bar 的数量
        :param barPeriod:
        :return:
        """
        collection = {
            'bar_1min': self.ctpCol1minBar,
            'bar_1day': self.ctpCol1dayBar,
        }.get(collectionName)

        # 假设周期 barPeriod=7, barNum=10
        cursor = self.ctpCol1minBar.find({'symbol': symbol}).hint('symbol')
        amount = cursor.count()
        # 先取余数
        rest = amount % barPeriod

        # 总的需要载入的 bar 数量，保证数量的同时，每根bar的周期不会乱掉
        barAmount = barNum * barPeriod + rest

        loadDate = self.today
        loadBarNum = 0
        noDataDays = 0

        documents = []  # [ [day31bar1, day31bar2, ...], ... , [day9bar1, day1bar2, ]]
        while noDataDays <= 30:
            # 连续一个月没有该合约数据，则退出
            sql = {
                'symbol': symbol,
                'tradingDay': loadDate
            }
            # 获取一天的 1min bar
            cursor = collection.find(sql, {'_id': 0})
            count = cursor.count()

            if count != 0:
                # 有数据，加载数据
                noDataDays = 0
                doc = [i for i in cursor]
                doc.sort(key=lambda bar: bar['datetime'])
                documents.append(doc)
                loadBarNum += cursor.count()
                if loadBarNum > barAmount:
                    # 数量够了， 跳出循环
                    break
            else:
                # 没有任何数据
                noDataDays += 1
            # 往前追溯
            loadDate -= datetime.timedelta(days=1)

        # 翻转逆序
        documents.reverse()
        documents = list(chain(*documents))  # 衔接成一个 list

        # 加载指定数量barAmount的 bar
        l = []
        for d in documents[-barAmount:]:
            bar = VtBarData()
            bar.load(d)
            l.append(bar)

        return l

    def callStrategyFunc(self, strategy, func, params=None):
        """调用策略的函数，若触发异常则捕捉"""
        try:
            if params:
                func(params)
            else:
                func()
        except Exception:
            # 停止策略，修改状态为未初始化
            strategy.trading = False
            strategy.inited = False
            traceback.print_exc()
            # 发出日志
            preMsg = u'策略{}触发异常已停止'.format(strategy.name)
            errMsg = traceback.format_exc()
            content = u'{}\n{}'.format(preMsg, errMsg.decode('utf-8'))
            self.log.error(content)

    def processStopOrder(self, tick):
        """收到行情后处理本地停止单（检查是否要立即发出）"""
        vtSymbol = tick.vtSymbol

        # 首先检查是否有策略交易该合约
        if vtSymbol in self.tickStrategyDict:
            # 遍历等待中的停止单，检查是否会被触发
            # for so in self.workingStopOrderDict.values():
            #     if so.vtSymbol == vtSymbol:
            for so in self.getAllStopOrdersSorted(tick):
                longTriggered = so.direction == DIRECTION_LONG and tick.lastPrice >= so.price  # 多头停止单被触发
                shortTriggered = so.direction == DIRECTION_SHORT and tick.lastPrice <= so.price  # 空头停止单被触发

                if longTriggered or shortTriggered:
                    # 买入和卖出分别以涨停跌停价发单（模拟市价单）
                    if so.direction == DIRECTION_LONG:
                        price = tick.upperLimit
                    else:
                        price = tick.lowerLimit

                    so.status = STOPORDER_TRIGGERED
                    if so.volume > 0:
                        # 成交量 >0 时才是正式下单，否则只是触发价格事件
                        vtOrderID = self.sendOrder(so.vtSymbol, so.orderType, price, so.volume, so.strategy)
                        so.vtOrderID = vtOrderID
                    del self.workingStopOrderDict[so.stopOrderID]
                    so.strategy.onStopOrder(so)

    def getAllStopOrdersSorted(self, vtTick):
        """
        对全部停止单排序后
        :return:
        """
        longOpenStopOrders = []
        shortCloseStopOrders = []
        shortOpenStopOrders = []
        longCloseStopOrders = []
        stopOrders = []
        soBySymbols = [so for so in self.workingStopOrderDict.values() if so.vtSymbol == vtTick.vtSymbol]

        for so in soBySymbols:
            if so.direction == DIRECTION_LONG:
                if so.offset == OFFSET_OPEN:
                    # 买开
                    longOpenStopOrders.append(so)
                else:
                    # 卖空
                    shortCloseStopOrders.append(so)
            elif so.direction == DIRECTION_SHORT:
                if so.offset == OFFSET_OPEN:
                    # 卖开
                    shortOpenStopOrders.append(so)
                else:
                    # 买空
                    longCloseStopOrders.append(so)
            else:
                stopOrders.append(so)
                self.log.error(u'未知的停止单方向 {}'.format(so.direction))

        # 根据触发价排序，优先触发更优的
        # 买开
        longOpenStopOrders.sort(key=lambda so: (so.price, so.priority))
        # 平多
        shortCloseStopOrders.sort(key=lambda so: (so.price, -so.priority))
        # 开多
        shortOpenStopOrders.sort(key=lambda so: (so.price, -so.priority))
        shortOpenStopOrders.reverse()
        # 卖空
        longCloseStopOrders.sort(key=lambda so: (so.price, so.priority))
        longCloseStopOrders.reverse()

        stopOrders.extend(shortCloseStopOrders)
        stopOrders.extend(longCloseStopOrders)
        stopOrders.extend(longOpenStopOrders)
        stopOrders.extend(shortOpenStopOrders)

        # # 先撮合平仓单
        # if self.bar.open >= self.bar.close:
        #     # 阴线，撮合优先级 平仓单 > 多单
        #     stopOrders.extend(shortCloseStopOrders)
        #     stopOrders.extend(longCloseStopOrders)
        #     stopOrders.extend(longOpenStopOrders)
        #     stopOrders.extend(shortOpenStopOrders)
        # else:
        #     # 阳线，撮合优先级，平仓单 > 空单
        #     stopOrders.extend(longCloseStopOrders)
        #     stopOrders.extend(shortCloseStopOrders)
        #     stopOrders.extend(shortOpenStopOrders)
        #     stopOrders.extend(longOpenStopOrders)

        return stopOrders

    def saveCtaDB(self, sql, document):
        """
        将 cta 策略的数据保存到数据库
        :return:
        """

        self.ctaCol.find_one_and_update(sql, document, upsert=True)

    def createCtaCollection(self):
        """

        :return:
        """
        db = self.mainEngine.strategyDB

        if __debug__:
            import pymongo.database
            assert isinstance(db, pymongo.database.Database)

        colNames = db.collection_names()
        if CTA_COL_NAME not in colNames:
            # 还没创建 cta collection
            ctaCol = db.create_collection(CTA_COL_NAME)
        else:
            ctaCol = db[CTA_COL_NAME]

        # 尝试创建创建索引
        indexSymbol = IndexModel([('symbol', DESCENDING)], name='symbol', background=True)
        indexClass = IndexModel([('class', ASCENDING)], name='class', background=True)
        indexDatetime = IndexModel([('datetime', DESCENDING)], name='datetime', background=True)

        indexes = [indexSymbol, indexClass, indexDatetime]
        self.mainEngine.createCollectionIndex(ctaCol, indexes)

    def initAll(self):
        try:
            super(CtaEngine, self).initAll()

            # 查询手续费率
            self.initQryCommissionRate()

            # 加载品种保证金率
            self.initQryMarginRate()

        except Exception as e:
            err = e.message
            self.log.critical(err)
            raise

    def initQryMarginRate(self):
        for s in self.strategyDict.values():
            count = 0
            while s._marginRate is None:
                if count % 3000:
                    # 30秒超时
                    err= u'加载品种 {} 保证金率失败'.format(s.vtSymbol)
                    self.log.warning(err)
                    # ctpGateway = self.mainEngine.getGateway('CTP')
                    # ctpGateway.close()
                    # ctpGateway.connect()
                    # time.sleep(5)

                if count % 30 == 0:
                    # 每3秒重新发送一次
                    self.log.info(u'尝试加载 {} 保证金率'.format(s.vtSymbol))
                    self.mainEngine.qryMarginRate('CTP', s.vtSymbol)

                # 每0.1秒检查一次返回结果
                time.sleep(0.1)
                count += 1

    def initQryCommissionRate(self):
        for s in list(self.strategyDict.values()):
            count = 0
            # 每个合约都要重新强制查询
            s.commissionRate = None

            while s.commissionRate is None:
                if count % 3000:
                    # 30秒超时
                    self.log.warning(u'加载品种 {} 手续费率超时'.format(str(s.vtSymbol)))
                    # self.log.warning(u'ctpGateway 重连')
                    # ctpGateway = self.mainEngine.getGateway('CTP')
                    # ctpGateway.close()
                    # ctpGateway.connect()
                    # time.sleep(5)

                if count % 30 == 0:
                    # 每3秒重新发送一次
                    self.log.info(u'尝试加载 {} 手续费率'.format(s.vtSymbol))
                    self.mainEngine.qryCommissionRate('CTP', s.vtSymbol)

                # 每0.1秒检查一次返回结果
                time.sleep(0.1)
                count += 1

    def stop(self):
        """
        程序停止时退出前的调用
        :return:
        """
        self.stopAll()

    def stopStrategy(self, name):
        super(CtaEngine, self).stopStrategy(name)
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            strategy.onStop()
