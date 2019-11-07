import tradersbot as tt
import random
import numpy as np 
import sklearn as sk
import pandas as pd

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
	'PRICE' 	    : {}, # stores current price of each security
	'BIDS'			: {}, # stores current best bids
	'OFFERS'		: {}, # stores current best offers
	'PREDS'			: {}, # stores active predictions (sorted by time) in (price, time, source) form
	'TIME'			: None 
}

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

	_cancel_open_orders(order)
	_make_good_trades(order)
	_exit_old_trades(order)

	# for security in positions.keys():
	# 	if len(open_orders) > ORDER_LIMIT:
	# 		break;
	# 	if abs(positions[security]) >= POS_LIMIT:
	# 		continue;
	# 	if random.random() < 0.5:
	# 		quant = min(10*random.randint(1, 10), POS_LIMIT-positions[security])
	# 		if quant < 0:
	# 			continue
	# 		order.addBuy(security, quantity=quant,price=SECURITIES[security])
	# 	else:
	# 		quant = min(10*random.randint(1, 10), positions[security]+POS_LIMIT)
	# 		if quant < 0:
	# 			continue
	# 		order.addSell(security, quantity=quant,price=SECURITIES[security])

# Update store of predictions
# You may want to change the way predictions are stored
def news_method(msg, order):
	log_obj.write(str(msg) + '\n')
	global CURRENT

	info = msg['news']['headline'].split()
	security = info[0]
	new_time = float(info[1])
	price = float(msg['news']['body']);
	CURRENT['PREDS'][security].append((price, new_time, msg['news']['source']))

	curr_bid = CURRENT['BIDS'][security]
	curr_ask = CURRENT['OFFERS'][security]

	fair_bid = price # how much we are willing to bid
	fair_ask = price # how much we are willing to offer
	assert(fair_bid <= fair_ask)

	print("FAIRS are: ", fair_bid, fair_ask)
	print("CURRENT MARKET: ", security, curr_bid, curr_ask)

	## TODO: add reliability into the trade
	if curr_bid > fair_ask:
		quant = POS_LIMIT + CURRENT['POSITIONS'][security] ## assumes we just buy to the max (only if good info)
		order.addSell(security, quantity=quant, price=fair_ask)
	if curr_ask < fair_bid:
		quant = POS_LIMIT - CURRENT['POSITIONS'][security]
		order.addBuy(security, quantity=quant, price=fair_bid)

def _update_fairs():
	## TODO: update these to use info and/or betas
	fairs = {}
	for security in CURRENT['POSITIONS'].keys():
		curr_bid = CURRENT['BIDS'][security]
		curr_ask = CURRENT['OFFERS'][security]
		fairs[security] = (curr_bid, curr_ask)

	return fairs

def _make_good_trades(order):
	## makes trades that are good to fair if there is still position limit / order limit

	# if len(CURRENT['OPEN_ORDERS']) > ORDER_LIMIT:
	# 	print("OVER ORDER_LIMIT")
	# 	return; ## TODO: change this so that it actively cancels stale open orders

	new_fairs = _update_fairs() # dict with (security: (bid, ask))

	for security in CURRENT['POSITIONS'].keys():
		fair_bid, fair_ask = new_fairs[security]
		curr_bid = CURRENT['BIDS'][security]
		curr_ask = CURRENT['OFFERS'][security]

		if curr_bid > fair_ask:
			quant = POS_LIMIT + CURRENT['POSITIONS'][security] ## assumes we just buy to the max (only if good info)
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
	for order_id, _ in CURRENT['OPEN_ORDERS']:
		order.addCancel(order_id)

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