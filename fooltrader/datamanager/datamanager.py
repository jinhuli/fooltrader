import argparse
import logging
import os
from multiprocessing import Process

import pandas as pd
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from fooltrader import settings
from fooltrader.api import event
from fooltrader.api.finance import get_balance_sheet_items, get_income_statement_items, get_cash_flow_statement_items
from fooltrader.api.quote import get_security_list, get_latest_download_trading_date, get_trading_dates, \
    get_available_tick_dates
from fooltrader.contract.files_contract import get_balance_sheet_path, get_income_statement_path, \
    get_cash_flow_statement_path
from fooltrader.settings import STOCK_START_CODE, STOCK_END_CODE
from fooltrader.spiders.security_list_spider import SecurityListSpider
from fooltrader.spiders.stock_finance_report_event_spider import StockFinanceReportEventSpider
from fooltrader.spiders.stock_finance_spider import StockFinanceSpider
from fooltrader.spiders.stock_kdata_spider import StockKDataSpider
from fooltrader.spiders.stock_kdata_spider_163 import StockKdataSpider163
from fooltrader.spiders.stock_tick_spider import StockTickSpider
from fooltrader.utils.utils import get_current_report_date

logger = logging.getLogger(__name__)


def crawl(spider, setting):
    process = CrawlerProcess({**get_project_settings(), **setting})
    process.crawl(spider)
    process.start()


def process_crawl(spider, setting):
    p = Process(target=crawl, args=(spider, setting))
    p.start()
    p.join(5 * 60)


def crawl_finance_data(start_code=STOCK_START_CODE, end_code=STOCK_END_CODE):
    for _, security_item in get_security_list(start=start_code, end=end_code).iterrows():
        try:
            # 先抓事件,有些后续抓取依赖事件
            process_crawl(StockFinanceReportEventSpider, {"security_item": security_item})

            current_report_date = get_current_report_date()

            # 资产负债表
            path = get_balance_sheet_path(security_item)
            if not os.path.exists(path):
                process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                   "report_type": "balance_sheet"})
            else:
                for balance_sheet_item in get_balance_sheet_items(security_item):
                    # 当前报告期还没抓取
                    if balance_sheet_item['reportDate'] != current_report_date:
                        # 报告出来了
                        df = event.get_finance_report_event(security_item, index='reportDate')
                        if current_report_date in df.index:
                            process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                               "report_type": "balance_sheet"})
                    break

            # 利润表
            path = get_income_statement_path(security_item)
            if not os.path.exists(path):
                process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                   "report_type": "income_statement"})
            else:
                for balance_sheet_item in get_income_statement_items(security_item):
                    if balance_sheet_item['reportDate'] != current_report_date:
                        # 报告出来了
                        df = event.get_finance_report_event(security_item, index='reportDate')
                        if current_report_date in df.index:
                            process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                               "report_type": "income_statement"})
                    break

            # 现金流量表
            path = get_cash_flow_statement_path(security_item)
            if not os.path.exists(path):
                process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                   "report_type": "cash_flow"})
            else:
                for balance_sheet_item in get_cash_flow_statement_items(security_item):
                    if balance_sheet_item['reportDate'] != current_report_date:
                        # 报告出来了
                        df = event.get_finance_report_event(security_item, index='reportDate')
                        if current_report_date in df.index:
                            process_crawl(StockFinanceSpider, {"security_item": security_item,
                                                               "report_type": "cash_flow"})
                    break
        except Exception as e:
            logger.error(e)


def crawl_stock_data(start_code=STOCK_START_CODE, end_code=STOCK_END_CODE):
    # 更新股票列表
    # TODO:看是否有必要判断有新股上市，目前每天抓一次列表，问题不大
    if False:
        logger.info('download stock list start')
        process_crawl(SecurityListSpider, {})
        logger.info('download stock list finish')

    for _, security_item in get_security_list(start=start_code, end=end_code).iterrows():
        # 抓取日K线
        logger.info("{} get kdata start".format(security_item['code']))

        start_date = get_latest_download_trading_date(security_item, source='163')
        end_date = pd.Timestamp.today()
        if start_date > end_date:
            logger.info("{} kdata is ok".format(security_item['code']))
        else:
            process_crawl(StockKdataSpider163, {"security_item": security_item,
                                                "start_date": start_date,
                                                "end_date": end_date})

        logger.info("{} get kdata from 163 end".format(security_item['code']))

        base_dates = set(get_trading_dates(security_item, source='163'))
        for fuquan in ('bfq', 'hfq'):
            sina_dates = set(get_trading_dates(security_item, source='sina', fuquan=fuquan))
            diff_dates = base_dates - sina_dates
            if diff_dates:
                logger.info("{} get {} kdata from sina start".format(security_item['code'], fuquan))
                process_crawl(StockKDataSpider, {"security_item": security_item,
                                                 "trading_dates": diff_dates,
                                                 "fuquan": fuquan})
                logger.info("{} get {} kdata from sina end".format(security_item['code'], fuquan))
            else:
                logger.info("{} {} kdata from sina is ok".format(security_item['code'], fuquan))

        # 抓取tick
        tick_dates = {x for x in base_dates if x >= settings.START_TICK_DATE}
        diff_dates = tick_dates - set(get_available_tick_dates(security_item))

        if diff_dates:
            logger.info("{} get tick start".format(security_item['code']))
            process_crawl(StockTickSpider, {"security_item": security_item,
                                            "trading_dates": diff_dates})
            logger.info("{} get tick end".format(security_item['code']))
        else:
            logger.info("{} tick is ok".format(security_item['code']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--start_code', default='000002', help='the stock start code to be crawled')
    parser.add_argument('-e', '--end_code', default='000002', help='the stock end code to be crawled')

    args = parser.parse_args()

    crawl_stock_data(args.start_code, args.end_code)
    # crawl_finance_data(args.start_code, args.end_code)
