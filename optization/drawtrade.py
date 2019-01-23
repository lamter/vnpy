# coding=utf-8
import arrow
from myplot.kline import *
import myplot.kline as mk
import json
from mystring import MyConfigParser

logging.basicConfig(level=logging.INFO)


class DrawTrade(object):
    """
    绘制模拟盘/实盘的成交图
    """

    def __init__(self, configPath='drawtrade.ini', startTradingDay=None, endTradingDay=None):
        """

        :param configPath:
        :param startTradingDay: 优先级 arg > *.ini > 成交单
        :param endTradingDay:
        """
        self.config = MyConfigParser()
        with open(configPath, 'r') as f:
            self.config.readfp(f)

        self.sql = dict(self.config.autoitems('trade_filter'))

        # K线的选取范围，也决定了成交图的范围
        self.underlyingSymbol = self.config.autoget('DrawTrade', 'underlyingSymbol')
        self.dropTradeIDsFile = self.config.autoget('DrawTrade', 'dropTradeIDsFile')
        self.drawFile = self.config.autoget('DrawTrade', 'drawfile')

        # 截止的始末日期
        if startTradingDay:
            self.startTradingDay = startTradingDay
        else:
            std = self.config.autoget('DrawTrade', 'startTradingDay')
            self.startTradingDay = arrow.get('{} 00:00:00+08'.format(std)).datetime if std else None

        if endTradingDay:
            self.endTradingDay = endTradingDay
        else:
            std = self.config.autoget('DrawTrade', 'endTradingDay')
            self.endTradingDay = arrow.get('{} 00:00:00+08'.format(std)).datetime if std else None

        self.bars = None  # K 线数据
        self.originTrl = None  # 成交单

    def loadBar(self):
        kwarg = dict(self.config.autoitems('ctp_mongo'))

        if self.matcher:
            # 优先使用默认的日期，默认日期为 None 时使用成交单的日期
            startTradingDay = self.startTradingDay or self.matcher.startTradingDay
            logging.info(u'根据成交单范围选取 K线 startTradingDay: {}'.format(startTradingDay))
            endTradingDay = self.endTradingDay or self.matcher.endTradingDay
            logging.info(u'根据成交单范围选取 K线 endTradingDay: {}'.format(endTradingDay))
        else:
            startTradingDay = self.startTradingDay
            endTradingDay = self.endTradingDay

        self.bars = mk.qryBarsMongoDB(
            self.underlyingSymbol,
            startTradingDay=startTradingDay,
            endTradingDay=endTradingDay,
            **kwarg
        )

    def loadTrade(self):
        """

        :return:
        """
        # 加载原始成交单
        kwarg = dict(self.config.autoitems('cta_mongo'))

        self.matcher = qryTradeListMongodb(
            sql=self.sql,
            **kwarg
        )

    def filterTrade(self):
        """
        过滤掉成交单中的东西
        :return:
        """
        # col = ['tradeID', 'datetime', 'offset', 'direction', 'price', 'volume', 'pos']

        # 如果是指定日期
        df = self.matcher.df.copy()
        if self.startTradingDay:
            df = df[df.datetime >= self.startTradingDay]
            if df.iloc[0].offset != u'开仓':
                df = self.matcher.df = df.iloc[1:]
        if self.endTradingDay:
            df = df[df.datetime <= self.endTradingDay]
        # 剔除指定的 TradeID
        # with open('/Users/lamter/workspace/SlaveO/svnpy/optization/droptradeid.json', 'r') as f:
        with open(self.dropTradeIDsFile, 'r') as f:
            lis = json.load(f)
            for d in lis:
                if d['name'] == self.sql['name'] and d['symbol'] == self.sql['symbol']:
                    _filter = []
                    tradeIDs = d['tradeID'][:]
                    for index in df.tradeID.index:
                        dfTradeID = df.tradeID.loc[index].strip(' ')
                        r = not dfTradeID in tradeIDs
                        _filter.append(r)
                        if not r:
                            tradeIDs.remove(dfTradeID)
                    if _filter:
                        logging.info(u'\t{symbol}\t{name}\t剔除成交'.format(**d))
                        df = df[pd.Series(_filter, df.index)]
                    # 每次只处理一个合约
                    break

        # 重新生成实例 DealMatcher 计算，不能直接替换 matcher.df 计算
        matcher = DealMatcher(df)
        matcher.do()
        self.originTrl = matcher.originTrl

    def draw(self, period='1T'):
        """
        重新绘制成交图
        :return:
        """

        tradeOnKlinePlot = tradeOnKLine(period, self.bars, self.originTrl, title=self.title, width=3000, height=1350)

        tradeOnKlinePlot.render(self.drawFile)

    @property
    def title(self):
        return u'成交' + u' '.join(self.sql.values())


    @staticmethod
    def sample_run():
        """
        示例如何使用本类
        :return:
        """
        # 配置文件
        configPath = 'drawtrade.ini'
        # 最优先的始末日期，比其他地方的始末日期都高的优先级
        startTradingDay = None
        endTradingDay = None

        d = DrawTrade(configPath, startTradingDay, endTradingDay)

        # 加载成交单
        d.loadTrade()
        # 剔除成交单中的异常的成交，比如手动平仓手不能凑成对的成交单
        d.filterTrade()

        # 加载完成交单后，再加载 K 线，这样K线可以根据成交单的时间范围进行少量读取
        d.loadBar()

        # 绘制图，可以选取K线周期，尽量选择能够被1小时整除的周期，或者日线，如
        # 1T, 5T, 15T, 30T, 1H , 1D之类
        d.draw('10T')


if __name__ == '__main__':
    dt = DrawTrade({})
    dt.loadBar()
    dt.loadTrade()
    dt.filterTrade()
