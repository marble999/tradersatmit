import tradersbot as tt
import random
import numpy as np 
import sklearn as sk
import pandas as pd
import pickle

from copy import deepcopy

###########################################################
# Make sure you run pip install tradersbot before running #
###########################################################

# Make a tradersbot
t = tt.TradersBot(host='127.0.0.1', id='trader2', password='trader2')

# Constants
POS_LIMIT = 500
ORDER_LIMIT = 100

# Hyperparameters
START_RELIABILITY = 30
MIN_RELIABILITY = 15
MAX_DIFFERENCE = 20

DEFAULT_CONFIDENCE = 20

# Variables
HISTORY = [] # stores all historical values, updated every 0.5 seconds
CURRENT = {
    'POSITIONS'     : {}, # stores current positions
    'OPEN_ORDERS'   : {}, # stores current open orders [no clue how these are stored lol]
    'PRICE'         : {}, # stores current price of each security
    'BIDS'          : {}, # stores current best bids
    'OFFERS'        : {}, # stores current best offers
    'PREDS'         : {}, # stores active predictions (sorted by time) in (price, time, source) form
    'TIME'          : 0, 
    'LAST_NEWS_TIME': -10 # stores last time news came out
}
SOURCES = {} # stores all preds sorted by sources in (security, price, new_time) form

# logging
log_file = 'msglog.txt'
log_obj = open(log_file, "w")

# Initializes the prices
# Initializes prediction dictionary
def ack_register_method(msg, order):
    log_obj.write(str(msg) + '\n')
    global CURRENT
    security_dict = msg['case_meta']['securities']
    for security in security_dict.keys():
        if not(security_dict[security]['tradeable']): 
            continue
        CURRENT['PRICE'][security] = security_dict[security]['starting_price']

    for security in security_dict:
        CURRENT['PREDS'][security] = [];

    print("Welcome to the exchange!!")

# Updates latest price and time
def market_update_method(msg, order):
    log_obj.write(str(msg) + '\n')
    global CURRENT, time

    security = msg['market_state']['ticker']

    # Gets the price by averaging the highest bid (or buy order)
    # and lowest ask (or sell order)
    max_bid = -1;
    min_ask = -1;
    for bid in msg['market_state']['bids']:
        if float(bid) > max_bid:
            max_bid = float(bid);
    
    for ask in msg['market_state']['asks']:
        if min_ask == -1 or float(ask) < min_ask:
            min_ask = float(ask);

    # update CURRENT data
    if min_ask == -1 or max_bid == -1:
        CURRENT['OFFERS'][security] = None
        CURRENT['BIDS'][security] = None
        CURRENT['PRICE'][security] = msg['market_state']['last_price'];
    else:
        CURRENT['OFFERS'][security] = min_ask
        CURRENT['BIDS'][security] = max_bid
        CURRENT['PRICE'][security] = (min_ask + max_bid) / 2;

    # Sets the time
    CURRENT['TIME'] = msg['elapsed_time']

    _info_arb_trades(order)
    # _momentum_trades(order)
    _exit_old_trades(order)

# Checks to make sure does not violate position limits or order limit
def trader_update_method(msg, order):
    log_obj.write(str(msg) + '\n')
    global CURRENT, HISTORY

    HISTORY.append(deepcopy(CURRENT)) # make a copy of historical data

    CURRENT['POSITIONS'] = msg['trader_state']['positions']
    CURRENT['OPEN_ORDERS'] = msg['trader_state']['open_orders']

    _cancel_open_orders(order)
    # _momentum_trades(order)
    _info_arb_trades(order)
    _exit_old_trades(order)

    historical_prices = _get_historical_prices()[0]
    np.savetxt("history.csv", historical_prices, delimiter=",")
    pickle.dump(HISTORY, open("history.pkl", 'wb'))

def news_method(msg, order):
    log_obj.write(str(msg) + '\n')
    global CURRENT, SOURCES

    info = msg['news']['headline'].split()
    security = info[0]
    new_time = float(info[1])
    price = float(msg['news']['body'])
    source = msg['news']['source']

    CURRENT['PREDS'][security].append((price, new_time, source))
    if source in SOURCES.keys():
        SOURCES[source].append((security, price, new_time))
    else:
        SOURCES[source] = [(security, price, new_time)]

    ## update new "news" time
    CURRENT['LAST_NEWS_TIME'] = CURRENT['TIME']

    _info_arb_trades(order)

def _update_fairs():
    reliability = _estimate_reliability()
    rho, stdevs = _estimate_rho() 

    ## TODO: use this info from above

    fairs = {}
    for security in CURRENT['POSITIONS'].keys():
        curr_bid = CURRENT['BIDS'][security]
        curr_ask = CURRENT['OFFERS'][security]

        fair_pred = None
        closest_time = 100000 # effective INF
        closest_source = None
        for price, new_time, source in CURRENT['PREDS'][security]: # find closest, good pred
            if new_time <= closest_time and reliability[source] <= MIN_RELIABILITY:
                fair_pred = price
                closest_time = new_time
                closest_source = source

        if fair_pred is None:
            fairs[security] = (curr_bid, curr_ask, DEFAULT_CONFIDENCE)
        else:
            ci = reliability[closest_source]
            fairs[security] = (fair_pred - ci, fair_pred + ci, ci)

        # print("Computing Fair for ", security)
        # print("PREDS:", CURRENT['PREDS'][security])
        # print(fairs[security])
        # print("CURRENT BBO", CURRENT['BIDS'][security], CURRENT['OFFERS'][security])

    return fairs

def _get_historical_prices():
    times = []
    data = np.empty((len(HISTORY), len(CURRENT['POSITIONS'].keys()))) # (security, time)
    for i, entry in enumerate(HISTORY):
        times.append(entry['TIME'])
        for j, security in enumerate(CURRENT['POSITIONS'].keys()):
            data[i,j] = entry['PRICE'][security]

    return np.array(data), np.array(times)

def _estimate_reliability():
    ## measures the mean square error of predictions made by this individual
    reliability = {}
    data, times = _get_historical_prices()
    for source in SOURCES.keys():
        past_preds = SOURCES[source]
        errors = []
        for security, pred_price, new_time in past_preds:
            security_idx = list(CURRENT['POSITIONS'].keys()).index(security)
            if new_time < CURRENT['TIME']:
                time_begin = np.searchsorted(times, new_time)
                time_end = np.searchsorted(times, min(new_time+5, CURRENT['TIME']))
                real_price = data[time_begin:time_end, security_idx].mean() # avg over 5 sec
                errors.append(real_price - pred_price)
        if len(errors) == 0:
            reliability[source] = START_RELIABILITY
        else:
            reliability[source] = np.sqrt((np.array(errors) ** 2).mean()) # mean square error

    return reliability
    
def _estimate_rho():
    data, times = _get_historical_prices()
    corr = np.corrcoef(data.T)
    rho = np.median(corr.reshape(-1))
    stdevs = np.std(data, axis=0)
    return rho, stdevs ## avg corr (should be pretty close to the right answer)

def _info_arb_trades(order):
    ## makes trades that are good to fair if there is still position limit / order limit

    # if len(CURRENT['OPEN_ORDERS']) > ORDER_LIMIT:
    #     print("OVER ORDER_LIMIT")
    #     return; ## TODO: change this so that it actively cancels stale open orders

    new_fairs = _update_fairs() # dict with (security: (bid, ask))
    # print(CURRENT['TIME'], new_fairs)

    for security in CURRENT['POSITIONS'].keys():
        fair_bid, fair_ask, confidence = new_fairs[security]
        curr_bid = CURRENT['BIDS'][security]
        curr_ask = CURRENT['OFFERS'][security]

        if confidence < DEFAULT_CONFIDENCE: ## only if we have reliable info
            if curr_bid > fair_ask:
                ## assumes we just buy to the max (only if good info)
                quant = min(200, POS_LIMIT + CURRENT['POSITIONS'][security])
                if quant > 10:
                    order.addSell(security, quantity=quant, price=fair_ask)
            if curr_ask < fair_bid:
                quant = min(200, POS_LIMIT - CURRENT['POSITIONS'][security])
                if quant > 10:
                    order.addBuy(security, quantity=quant, price=fair_bid)

def _momentum_trades(order):
    THRESHOLD = 1.0

    data, times = _get_historical_prices()
    if (CURRENT['TIME'] - CURRENT['LAST_NEWS_TIME'] >= 2):
        if (CURRENT['TIME'] - CURRENT['LAST_NEWS_TIME'] <= 5):
            for j, security in enumerate(CURRENT['POSITIONS'].keys()):
                news_idx = np.searchsorted(times, CURRENT['LAST_NEWS_TIME'])
                net_change_in_price =  CURRENT['PRICE'][security] - HISTORY[news_idx]['PRICE'][security]
                
                if (net_change_in_price > THRESHOLD):
                    print(CURRENT['TIME'], security, net_change_in_price)
                    curr_ask = CURRENT['OFFERS'][security]
                    quant = min(100, POS_LIMIT + CURRENT['POSITIONS'][security])
                    if quant > 10:
                        order.addBuy(security, quantity=quant, price=CURRENT['PRICE'][security])

                elif (net_change_in_price < -THRESHOLD):
                    print(CURRENT['TIME'], security, net_change_in_price)
                    curr_bid = CURRENT['BIDS'][security]
                    quant = min(100, POS_LIMIT - CURRENT['POSITIONS'][security])
                    if quant > 10:
                        order.addSell(security, quantity=quant, price=CURRENT['PRICE'][security])

def _exit_old_trades(order):
    ## gets out of stale positions when info expires
    ## TODO: maybe adjust this for stuff that you have future info on
    reliability = _estimate_reliability()

    for security in CURRENT['POSITIONS'].keys():
        for price, time, source in CURRENT['PREDS'][security]:
            if CURRENT['TIME'] > time and reliability[source] < MIN_RELIABILITY: ## TODO: only cancel for reliable news
                print("Clearing position for ", security, "at time ", CURRENT['TIME'])
                ## TODO: what happens if these orders dont go through?
                if CURRENT['POSITIONS'][security] > 0:
                    curr_bid = CURRENT['BIDS'][security]
                    order.addSell(security, quantity=CURRENT['POSITIONS'][security], price=curr_bid)
                if CURRENT['POSITIONS'][security] < 0:
                    curr_ask = CURRENT['OFFERS'][security]
                    order.addBuy(security, quantity=CURRENT['POSITIONS'][security], price=curr_ask)
                CURRENT['PREDS'][security].remove((price, time, source))

def _cancel_open_orders(order):
    # print("CURRENT ORDERS", CURRENT['OPEN_ORDERS'])
    for order_id in CURRENT['OPEN_ORDERS'].keys():
        ticker = CURRENT['OPEN_ORDERS'][order_id]['ticker']
        print("CANCEL", ticker, order_id)
        order.addCancel(ticker=ticker, orderId=int(order_id))

###############################################
#### You can add more of these if you want ####
###############################################

t.onAckRegister = ack_register_method
t.onMarketUpdate = market_update_method
t.onTraderUpdate = trader_update_method
t.onNews = news_method
#t.onTrade = trade_method
#t.onAckModifyOrders = ack_modify_orders_method
t.run()