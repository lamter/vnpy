# encoding: UTF-8

"""
绘制成交图
"""

import logging.config
import logging
import time
import multiprocessing
from threading import Thread

import myplot.kline as mk
import arrow
import pymongo
from mystring import MyConfigParser

from opt_server import OptimizeService
from opt_boss import OptBoss
from . import backtestingarg

# 输出日志的级别
logging.basicConfig(level=logging.INFO)


class DrawBacktestingTrade(object):
    def __init__(self, configPath='drawtrade.ini', startTradingDay=None, endTradingDay=None):
        self.config = MyConfigParser()
        with open(configPath, 'r') as f:
            self.config.read_file(f)

        # 生成批量参数使用哪个文件
        self.argFileName = self.config.autoget('DrawBacktestingTrade', 'argFileName')
        # 批量优化使用的配置文件
        self.optfile = self.config.autoget('DrawBacktestingTrade', 'optfile')

        # Mongodb 数据库配置
        self.host = self.config.autoget('backtesting_mongo', 'host')
        self.port = self.config.autoget('backtesting_mongo', 'port')
        self.username = self.config.autoget('backtesting_mongo', 'username')
        self.password = self.config.autoget('backtesting_mongo', 'password')
        self.dbn = self.config.autoget('backtesting_mongo', 'dbn')
        self.btinfo = self.config.autoget('backtesting_mongo', 'btinfo')
        self.btarg = self.config.autoget('backtesting_mongo', 'btarg')
        self.btresult = self.config.autoget('backtesting_mongo', 'btresult')

        self.client = pymongo.MongoClient(self.host, self.port)
        self.db = self.client[self.dbn]
        self.db.authenticate(self.username, self.password)
        self.btinfoCol = self.db[self.btinfo]
        self.btargCol = self.db[self.btarg]
        self.btresultCol = self.db[self.btresult]

        # startTradingDay = self.config.autoget('DrawBacktestingTrade', 'startTradingDay')
        # endTradingDay = self.config.autoget('DrawBacktestingTrade', 'endTradingDay')
        # self.startTradingDay = arrow.get(
        #     '{} 00:00:00+08:00'.format(startTradingDay)).datetime if startTradingDay else None
        # self.endTradingDay = arrow.get(
        #     '{} 00:00:00+08:00'.format(endTradingDay)).datetime if endTradingDay else None

        self.originTrl = None
        self.originIndLine = None

        # K线的选取范围，也决定了成交图的范围
        self.startTradingDay = startTradingDay
        self.endTradingDay = endTradingDay

    @property
    def backtestingdrawfile(self):
        return self.config.autoget('DrawBacktestingTrade', 'backtestingdrawfile')

    @property
    def underlyingSymbol(self):
        return self.config.autoget('DrawBacktestingTrade', 'underlyingSymbol')

    @property
    def optsv(self):
        return self.config.autoget('DrawBacktestingTrade', 'optsv')

    def clearCollection(self):
        """
        运行清空命令
        :return:
        """
        self.btinfoCol.delete_many({})
        self.btargCol.delete_many({})
        self.btresultCol.delete_many({})

    def runArg(self):
        logging.info('即将使用 {} 的配置'.format(self.optfile))
        b = backtestingarg.BacktestingArg(self.argFileName, self.optfile)
        logging.info('生成参数')
        b.start()

    def runBacktesting(self):
        """


        :return:
        """
        logging.root.setLevel(logging.WARNING)

        logging.info(u'启动 server')
        opt_server = OptimizeService(self.optfile)
        opt_server.start()

        logging.info(u'启动 BOSS ')
        opt_boss = OptBoss(self.optfile)
        opt_boss.start()
        opt_boss_threading = Thread(target=opt_boss.run)
        opt_boss_threading.start()

        # 10秒没有新的任务完成判定为批量回测完成
        opt_server.set_auto_close(True)

        # 启动服务
        opt_server.run()

        # 关闭 boss
        opt_boss.stop()
        while opt_boss_threading.isAlive():
            pass

    @staticmethod
    def runOptBoss(optfile):
        """
        运行回测算力
        :return:
        """
        time.sleep(2)
        logging.info('启动批量回测算力')
        server = OptBoss(optfile)
        server.start()

    def loadBar(self):
        # 加载K线
        # 截取回测始末日期，注释掉的话默认取全部主力日期
        logging.info('加载 bar')
        kwargs = dict(self.config.autoitems('ctp_mongo'))

        self.bars = mk.qryBarsMongoDB(
            underlyingSymbol=self.underlyingSymbol,
            startTradingDay=self.startTradingDay,
            endTradingDay=self.endTradingDay,
            **kwargs
        )

    def loadTrade(self):
        """
        加载成交单
        :return:
        """
        logging.info('从 {} 加载 成交单'.format(self.btresult))
        self.originTrl = mk.qryBtresultMongoDB(
            underlyingSymbol=self.underlyingSymbol,
            optsv=self.optsv,
            host=self.host, port=self.port, dbn=self.dbn, collection=self.btresult, username=self.username,
            password=self.password,
            items={'成交单': 1}
        )
        if not self.originTrl:
            logging.warning('未获得成交单')

    def loadIndLine(self):
        """
        加载线型技术指标
        :return:
        """
        logging.info('从 {} 加载 技术指标'.format(self.btresult))
        self.originIndLine = mk.qryBtresultMongoDB(
            underlyingSymbol=self.underlyingSymbol,
            optsv=self.optsv,
            host=self.host, port=self.port, dbn=self.dbn, collection=self.btresult, username=self.username,
            password=self.password,
            items={'techIndLine': 1}
        )
        if not self.originIndLine:
            logging.warning('未获得线技术指标')

    def draw(self, period='1T', width=3000, height=1350):
        """
        绘制成交图
        :return:
        """
        tradeOnKlinePlot = mk.tradeOnKLine(period, self.bars, self.originTrl, self.originIndLine, title=self.title,
                                           width=width, height=height)
        if '{optsv}' in self.backtestingdrawfile:
            f = self.backtestingdrawfile.format(optsv=self.optsv)
        else:
            f = self.backtestingdrawfile
        logging.info('生成成交图 {}'.format(f))
        tradeOnKlinePlot.render(f)

    @property
    def title(self):
        return '回测' + '{}'.format(self.optsv)


if __name__ == '__main__':
    dbt = DrawBacktestingTrade()

    # 按照需要注释掉部分流程
    dbt.clearCollection()  # 清空数据库
    dbt.runArg()  # 生成参数

    # 批量回测
    dbt.runBacktesting()

    # 加载数据并绘制成交图
    dbt.loadBar()
    dbt.loadTrade()
    dbt.draw()
