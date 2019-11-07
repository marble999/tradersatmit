import tradersbot as tt
import random

###########################################################
# Make sure you run pip install tradersbot before running #
###########################################################

# Make a tradersbot
t = tt.TradersBot(host='127.0.0.1', id='trader1', password='trader1')

# Constants
POS_LIMIT = 500
ORDER_LIMIT = 100

# Keeps track of prices
SECURITIES = {}
PREDS = {}
time = 0
open_orders = {}

# logging
log_file = 'msglog.txt'

# Initializes the prices
# Initializes prediction dictionary
def ack_register_method(msg, order):
	global SECURITIES, PREDS, time
	security_dict = msg['case_meta']['securities']
	for security in security_dict.keys():
		if not(security_dict[security]['tradeable']): 
			continue
		SECURITIES[security] = security_dict[security]['starting_price']

	for security in security_dict:
		PREDS[security] = {};

# Updates latest price and time
def market_update_method(msg, order):
	global SECURITIES, time

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
			min_ask =float(ask);

	if min_ask == -1 or max_bid == -1:
		price = msg['market_state']['last_price'];
	else:
		price = (min_ask + max_bid) / 2;
	SECURITIES[security] = price;

	# Sets the time
	time = msg['elapsed_time']

# Buys or sells in a random quantity every time it gets an update
# You do not need to buy/sell here
# Checks to make sure does not violate position limits or order limit
def trader_update_method(msg, order):
	global SECURITIES, POS_LIMIT, open_orders

	positions = msg['trader_state']['positions']
	open_orders = msg['trader_state']['open_orders']

	for security in positions.keys():
		if len(open_orders) > ORDER_LIMIT:
			break;
		if abs(positions[security]) >= POS_LIMIT:
			continue;
		if random.random() < 0.5:
			quant = min(10*random.randint(1, 10), POS_LIMIT-positions[security])
			if quant < 0:
				continue
			order.addBuy(security, quantity=quant,price=SECURITIES[security])
		else:
			quant = min(10*random.randint(1, 10), positions[security]+POS_LIMIT)
			if quant < 0:
				continue
			order.addSell(security, quantity=quant,price=SECURITIES[security])

# Update store of predictions
# You may want to change the way predictions are stored
def news_method(msg, order):
	global PREDS
	info = msg['news']['headline'].split()
	security = info[0]
	new_time = float(info[1])
	price = float(msg['news']['body']);
	PREDS[security][new_time] = price;



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