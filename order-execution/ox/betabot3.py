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
t = tt.TradersBot(host='127.0.0.1', id='trader5', password='trader5')

# Constants
POS_LIMIT = 500
ORDER_LIMIT = 100

# Hyperparameters
START_RELIABILITY = 30
MIN_RELIABILITY = 5
MAX_TRADE_SZ = 2000

DEFAULT_CONFIDENCE = 5

MAX_DIFFERENCE = 20

# Variables
CASE_LENGTH = None        # stores length of case (in seconds)
HISTORY = None            # stores all historical prices, updated every trader tick
HISTORY_TIMES = []        # stores the times for which we have history
SOURCES = {}              # stores all preds sorted by sources in (security, price, new_time) form
SECURITIES = None         # stores list of all securities for indexing

CURRENT = {
    'POSITIONS'     : {}, # stores current positions
    'OPEN_ORDERS'   : {}, # stores current open orders [no clue how these are stored lol]
    'PRICE'         : {}, # stores current price of each security
    'BIDS'          : {}, # stores current best bids
    'OFFERS'        : {}, # stores current best offers
    'PREDS'         : {}, # stores active predictions (sorted by time) in (price, time, source) form
    'FAIRS'         : None,  # stores tuple of (time, {security: price, ci, flag})
    'TIME'          : 2, # it's weird but trading only starts at time step 2...
    'LAST_NEWS_TIME': -10, # stores last time news came out
}

# Initializes the prices
# Initializes prediction dictionary
def ack_register_method(msg, order):
    global CURRENT, HISTORY, CASE_LENGTH, SECURITIES
    security_dict = msg['case_meta']['securities']
    for security in security_dict.keys():
        if not(security_dict[security]['tradeable']): 
            continue
        CURRENT['PRICE'][security] = security_dict[security]['starting_price']

    for security in security_dict:
        CURRENT['PREDS'][security] = [];

    CASE_LENGTH = msg['case_meta']['case_length']
    SECURITIES = list(security_dict.keys())
    HISTORY = np.empty((CASE_LENGTH+1, len(SECURITIES)))
    HISTORY[:,:] = np.nan

    print("Welcome to the exchange!!")

# Updates latest price and time
def market_update_method(msg, order):
    global CURRENT

    security = msg['market_state']['ticker']

    # Gets the price by averaging the highest bid (or buy order)
    # and lowest ask (or sell order)
    max_bid = -1;
    min_ask = -1;
    max_bid_size = None;
    min_ask_size = None;

    for bid in msg['market_state']['bids']:
        if float(bid) > max_bid:
            max_bid = float(bid);
            max_bid_size = msg['market_state']['bids'][bid]
    
    for ask in msg['market_state']['asks']:
        if min_ask == -1 or float(ask) < min_ask:
            min_ask = float(ask);
            min_ask_size = msg['market_state']['asks'][ask]

    # update CURRENT data
    if min_ask == -1 or max_bid == -1:
        CURRENT['OFFERS'][security] = None
        CURRENT['BIDS'][security] = None
        CURRENT['PRICE'][security] = msg['market_state']['last_price'];
    else:
        CURRENT['OFFERS'][security] = min_ask
        CURRENT['BIDS'][security] = max_bid
        # CURRENT['PRICE'][security] = (min_ask + max_bid) / 2;
        CURRENT['PRICE'][security] = (min_ask * max_bid_size + max_bid * min_ask_size) / (max_bid_size + min_ask_size);

    # Sets the time
    CURRENT['TIME'] = msg['elapsed_time']

# Checks to make sure does not violate position limits or order limit
def trader_update_method(msg, order):
    global CURRENT, HISTORY, HISTORY_TIMES

    # make a copy of historical data
    HISTORY[CURRENT['TIME'], :] = [CURRENT['PRICE'][security] for security in SECURITIES]
    HISTORY_TIMES.append(CURRENT['TIME'])

    CURRENT['POSITIONS'] = msg['trader_state']['positions']
    CURRENT['OPEN_ORDERS'] = msg['trader_state']['open_orders']

    CURRENT['FAIRS'] = _update_fairs()

    _exit_old_trades(order)
    _general_fair_value_arb(order)
    _cancel_open_orders(order)

    ## log historical prices for analysis
    historical_prices = _get_historical_prices()[0]
    np.savetxt("history.csv", historical_prices, delimiter=",")
    pickle.dump(HISTORY, open("history.pkl", 'wb'))

def news_method(msg, order):
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

    _instant_news_taking_arb(msg, order)

def _update_fairs():
    reliability = _estimate_reliability()
    rho, mus, stdevs = _estimate_rho() 

    fairs = {}
    market_impact = [] # (stores impact in stdevs, and confidence)

    ## compute next reliable pred
    closest_time = 100000 # effective INF
    for security in SECURITIES:
        for price, new_time, source in CURRENT['PREDS'][security]: # find closest, good pred
            if new_time <= closest_time and new_time > CURRENT['TIME'] and reliability[source] <= MIN_RELIABILITY:
                closest_time = new_time

    ## now compute fairs for every stock for that time

    time_remaining = closest_time - CURRENT['TIME']

    for i, security in enumerate(SECURITIES):
        curr_price = CURRENT['PRICE'][security]
        fair_pred = None
        news_source = None
        fair_pred_time = None

        for price, new_time, source in CURRENT['PREDS'][security]: # find closest, good pred
            if new_time >= closest_time and reliability[source] <= MIN_RELIABILITY: #allow future preds to be used too
                fair_pred = price
                news_source = source
                fair_pred_time = new_time

        if fair_pred is None:
            fairs[security] = (curr_price, DEFAULT_CONFIDENCE, "none")
        else:
            ci = reliability[news_source]
            fairs[security] = (fair_pred, ci, "news")
            time_remaining = fair_pred_time - CURRENT['TIME']
            net_impact_per_time = (fair_pred - curr_price) / time_remaining - mus[i]
            market_impact.append(((net_impact_per_time, ci / stdevs[i])))

    if CURRENT['TIME'] > 50 and len(market_impact) > 0:
        best_guess_market_impact = sum(el[0] for el in market_impact) / len(market_impact) # in stdevs
        best_guess_market_ci = sum(el[1] for el in market_impact) / len(market_impact)

        print("We have ", len(market_impact), "pieces of info")
        print("MARKET IMPACT (stdevs) is ", best_guess_market_impact)

        for i, security in enumerate(SECURITIES):
            if fairs[security][2] == 'none':
                curr_price = CURRENT['PRICE'][security]
                beta_pred = curr_price + (mus[i] + best_guess_market_impact * rho * stdevs[i]) * time_remaining
                fairs[security] = (beta_pred, 6/len(market_impact), "beta")

    return closest_time, fairs

def _get_historical_prices():
    return HISTORY[:CURRENT['TIME']+1], HISTORY_TIMES

def _estimate_reliability():
    ## measures the mean square error of predictions made by this individual
    reliability = {}
    data, times = _get_historical_prices()
    data = data[times, :]

    for source in SOURCES.keys():
        past_preds = SOURCES[source]
        errors = []
        for security, pred_price, new_time in past_preds:
            security_idx = SECURITIES.index(security)
            if new_time < CURRENT['TIME']:
                time_begin = np.searchsorted(times, new_time)
                time_end = np.searchsorted(times, min(new_time+5, CURRENT['TIME']))
                real_price = data[time_begin:time_end, security_idx].mean() # avg over 5 sec
                errors.append(real_price - pred_price)

        if len(errors) == 0:
            reliability[source] = START_RELIABILITY
        else:
            reliability[source] = np.sqrt((np.array(errors) ** 2).mean()) # mean square error

    print("reliability", reliability)
    return reliability
    
def _estimate_rho():
    data, times = _get_historical_prices()
    data = data[times, :] ## TODO: check that this doesnt accidentally remove some of the data

    returns = data[1:,:] - data[:-1,:]
    corr = np.corrcoef(returns.T)
    rho = np.median(corr.reshape(-1))

    stdevs = np.std(returns, axis=0)
    mus = np.mean(returns, axis=0)

    print("rho", rho)
    print("mu", mus)
    print("stdevs", stdevs)

    return rho, mus, stdevs 

def _instant_news_taking_arb(msg, order):
    pass

def _general_fair_value_arb(order):

    for security in CURRENT['POSITIONS'].keys():
        fair, ci, flag = CURRENT['FAIRS'][1][security]
        edge = ci / 2

        fair_bid = fair - edge
        fair_ask = fair + edge
        curr_bid = CURRENT['BIDS'][security]
        curr_ask = CURRENT['OFFERS'][security]

        if ci < DEFAULT_CONFIDENCE and flag != 'none': ## only if we have reliable info
            if curr_bid > fair_ask:
                quant = min(MAX_TRADE_SZ, POS_LIMIT + CURRENT['POSITIONS'][security])
                if quant > 10:
                    order.addSell(security, quantity=quant, price=fair_ask)
            if curr_ask < fair_bid:
                quant = min(MAX_TRADE_SZ, POS_LIMIT - CURRENT['POSITIONS'][security])
                if quant > 10:
                    order.addBuy(security, quantity=quant, price=fair_bid)

def _exit_old_trades(order):
    ## gets out of stale positions when info expires
    ## TODO: maybe adjust this for stuff that you have future info on
    global CURRENT

    reliability = _estimate_reliability()
    closest_time, fairs = CURRENT['FAIRS']

    AGGRESSIVENESS = 0.1

    print("EXIT OLD TRADES at time ", CURRENT['TIME'], "for time", closest_time)

    if CURRENT['TIME'] >= closest_time-1:
        
        for j, security in enumerate(SECURITIES):
            curr_price = CURRENT['PRICE'][security]
            pred_price = fairs[security]
            old_price = HISTORY[int(closest_time)-10, j]

            print(security, old_price, curr_price, pred_price, CURRENT["POSITIONS"][security]) # progress report


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