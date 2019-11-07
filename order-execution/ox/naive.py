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
t = tt.TradersBot(host='127.0.0.1', id='trader1', password='trader1')

# Constants
POS_LIMIT = 500
ORDER_LIMIT = 100

# Variables
HISTORY = [] # stores all historical values, updated every 0.5 seconds
CURRENT = {
    'POSITIONS'     : {}, # stores current positions
    'OPEN_ORDERS'   : {}, # stores current open orders [no clue how these are stored lol]
    'PRICE'         : {}, # stores current price of each security
    'BIDS'          : {}, # stores current best bids
    'OFFERS'        : {}, # stores current best offers
    'PREDS'         : {}, # stores active predictions (sorted by time) in (price, time, source) form
    'TIME'          : None 
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

    _make_good_trades(order)

# Checks to make sure does not violate position limits or order limit
def trader_update_method(msg, order):
    log_obj.write(str(msg) + '\n')
    global CURRENT, HISTORY

    HISTORY.append(deepcopy(CURRENT)) # make a copy of historical data

    CURRENT['POSITIONS'] = msg['trader_state']['positions']
    CURRENT['OPEN_ORDERS'] = msg['trader_state']['open_orders']

    # _cancel_open_orders(order)
    _make_good_trades(order)
    _exit_old_trades(order)

    # historical_prices = _get_historical_prices()[0]
    # np.savetxt("history.csv", historical_prices, delimiter=",")
    # pickle.dump(HISTORY, open("history.pkl", 'wb'))

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

    curr_bid = CURRENT['BIDS'][security]
    curr_ask = CURRENT['OFFERS'][security]

    fair_bid = price # how much we are willing to bid
    fair_ask = price # how much we are willing to offer
    assert(fair_bid <= fair_ask)

    print("FAIRS are: ", fair_bid, fair_ask)
    print("CURRENT MARKET: ", security, curr_bid, curr_ask)

    ## TODO: add reliability into the trade
    # if curr_bid > fair_ask:
    #     quant = POS_LIMIT + CURRENT['POSITIONS'][security] ## assumes we just buy to the max (only if good info)
    #     order.addSell(security, quantity=quant, price=fair_ask)
    # if curr_ask < fair_bid:
    #     quant = POS_LIMIT - CURRENT['POSITIONS'][security]
    #     order.addBuy(security, quantity=quant, price=fair_bid)

def _update_fairs():
    reliability = _estimate_reliability()
    rho = _estimate_rho() 

    ## TODO: use this info from above

    fairs = {}
    for security in CURRENT['POSITIONS'].keys():
        curr_bid = CURRENT['BIDS'][security]
        curr_ask = CURRENT['OFFERS'][security]

        fair_pred = None
        closest_time = 10000 # effective INF
        for price, new_time, source in CURRENT['PREDS'][security]:
            if new_time <= closest_time and reliability[source] <= 20: # get the closest decent pred
                fair_pred = price
                closest_time = new_time

        if fair_pred is None:
            fairs[security] = (curr_bid, curr_ask)
        else:
            print("Real edge!")
            ci = reliability[source] / 2
            fairs[security] = (fair_pred - ci, fair_pred + ci)

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
            reliability[source] = 10.0
        else:
            reliability[source] = np.sqrt((np.array(errors) ** 2).mean())

    print("RELIABILITY: ", str(reliability))
    return reliability
    

def _estimate_rho():
    data, times = _get_historical_prices()
    corr = np.corrcoef(data.T)
    rho = np.median(corr.reshape(-1))
    print("RHO: ", rho)
    return rho ## avg corr (should be pretty close to the right answer)

def _make_good_trades(order):
    ## makes trades that are good to fair if there is still position limit / order limit

    # if len(CURRENT['OPEN_ORDERS']) > ORDER_LIMIT:
    #     print("OVER ORDER_LIMIT")
    #     return; ## TODO: change this so that it actively cancels stale open orders

    new_fairs = _update_fairs() # dict with (security: (bid, ask))

    for security in CURRENT['POSITIONS'].keys():
        fair_bid, fair_ask = new_fairs[security]
        curr_bid = CURRENT['BIDS'][security]
        curr_ask = CURRENT['OFFERS'][security]

        if curr_bid > fair_ask:
            ## assumes we just buy to the max (only if good info)
            quant = POS_LIMIT + CURRENT['POSITIONS'][security] 
            order.addSell(security, quantity=quant, price=fair_ask)
        if curr_ask < fair_bid:
            quant = POS_LIMIT - CURRENT['POSITIONS'][security]
            order.addBuy(security, quantity=quant, price=fair_bid)

def _exit_old_trades(order):
    ## gets out of stale positions when info expires
    ## TODO: maybe adjust this for stuff that you have future info on 
    for security in CURRENT['POSITIONS'].keys():
        for price, time, source in CURRENT['PREDS'][security]:
            if CURRENT['TIME'] > time: ## TODO: only cancel for reliable news
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
    print("CURRENT ORDERS", CURRENT['OPEN_ORDERS'])
    for order_id in CURRENT['OPEN_ORDERS'].keys():
        ticker = CURRENT['OPEN_ORDERS'][order_id]['ticker']
        order.addCancel(ticker=ticker, orderId=order_id)

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