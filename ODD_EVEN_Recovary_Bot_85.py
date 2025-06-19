import asyncio
import json
import websockets
from datetime import datetime as dt
import datetime
import time


class DigitBot:
    def __init__(self, config):
        # Store configuration
        self.config = config
        
        # Bot trade state variables
        self.current_stake = config["stake"]
        self.base_stake = config["stake"]  # Keep track of base stake
        self.recovary_base_stake = config["martingale_stake"]
        self.wait_time_loss = config["wait_time_loss"]
        self.wait_time_win = config["wait_time"]
        self.recovery_wait_time = 2  # Fixed wait time for recovery trades (2 seconds)
        self.numbers_list = config["numbers_list"]
        
        # Balance tracking
        self.initial_balance = 0
        self.current_balance = 0
        
        self.currency = "USD"
        self.total_profit = 0
        self.trade_active = False
        self.last_trade_time = None
        
        # Sequential digit targeting
        self.current_target_digit = 0  # Start with digit 0
        self.target_sequence = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]  # Sequence to follow
        self.waiting_for_target = True  # Flag to indicate we're waiting for target digit
        
        # Recovery strategy variables
        self.in_recovery_mode = False
        self.recovery_sequence = []  # Will be populated based on last digit
        self.recovery_index = 0
        
        # WebSocket connections
        self.ws_data = None  # For data/ticks
        self.ws_trading = None  # For trading
        self.ws_trading_2 = None
        
        # Running flag
        self.running = True
        
        # Trade history
        self.trades_history = []
        
        # Last tick data
        self.last_digit = None
        self.last_digit_2 = -100
        self.resetor = -100
        self.result = ""
        self.first_time = True
        self.first_time_1 = True
        self.first_time_2 = True
        self.trade_type = 0
        self.amount_revcovered = 0
        self.profit_sum = 0
        self.amount_revcovered_sum = 0
        self.result_final = ""
        self.trade_time_indicator = None
        self.profit_plus = 0
        self.profit_minus = 0
        self.target_type = ""
        self.trade_open = False
        self.not_first_time_recovary = False
        self.mid_win = False
        self.tick_monitor_1 = None
        self.prev_digits = []
        self.over_digit = self.config["barrier_over"]
        self.under_digit = self.config["barrier_under"]
        self.recovery_sequence_even = self.config["even_sequence"]
        self.recovery_sequence_odd = self.config["odd_sequence"]
        self.prev_digits_count = self.config["prev_digits_count"]
        self.max_stake = self.config["max_stake"]
        self.step_one = True
        self.prev_digits_count_trade_type_1 = 2
        self.numbers_list_trade_type_1 = [4, 5]
        self.martingale_stake_number = 0
        self.martingale_stake_list = [0.35, 0.78, 1.64, 3.74, 7.31, 15.44]
        self.martingale_weight_list = [3, 6, 12, 24, 48, 96, 192]
        self.recovery_win_count = 0
        self.last_digits_amount = []
        self.digit_count = 20
        self.acceptable_precentage = 0.8
        self.digit_string = ""
        self.start = False
        self.checking_digit = None
        self.checking_bit = None


    async def connect_websockets(self):
        """Establish all necessary WebSocket connections"""
        try:
            # Connect to main data websocket
            self.ws_data = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            print("✓ Main WebSocket connected")
            
            # Connect to trading websocket
            self.ws_trading = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            self.ws_trading_2  = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            
            # Authenticate
            auth_response = await self.send_and_receive(self.ws_trading, {
                "authorize": self.config["api_token"]
            })

            # Authenticate
            auth_response_2 = await self.send_and_receive(self.ws_trading_2, {
                "authorize": self.config["api_token"]
            })
            
            if not auth_response or not auth_response.get("authorize"):
                print("✗ Authentication failed")
                return False
            
            if not auth_response_2 or not auth_response_2.get("authorize"):
                print("✗ Authentication failed")
                return False
            
            #trade_type = "Over 5" if self.config["is_over"] else "Under 4"
            print(f"✓ WebSocket authenticated")
            
            # Get account info
            authorize_data = auth_response.get("authorize", {})
            self.initial_balance = float(authorize_data.get('balance', 0))
            self.current_balance = self.initial_balance
            self.balance_previous = self.initial_balance
            self.balance_after = self.initial_balance
            
            # Use the currency from the account
            self.currency = authorize_data.get('currency', 'USD')
            
            # Display account information
            print(f"Currency: {self.currency}")
            print(f"Account - Initial Balance: ${self.initial_balance:.2f}")
            print(f"Account - LoginID: {authorize_data.get('loginid')}")
            
            # Subscribe to ticks
            await self.send_and_receive(self.ws_data, {
                "ticks": self.config["symbol"],
                "subscribe": 1  # Ensure we keep getting updates
            })
            
            return True
            
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    async def send_and_receive(self, websocket, message):
        """Helper function to send messages and receive responses"""
        try:
            await websocket.send(json.dumps(message))
            response = await websocket.recv()
            return json.loads(response)
        except Exception as e:
            print(f"WebSocket communication error: {e}")
            return None


    async def wait_until_start_time(self):
        """Wait until the scheduled start time before running the bot"""
        try:
            # Parse the configured start time
            start_datetime = dt.strptime(self.config["start_time"], "%d-%m-%Y %H:%M:%S")
            
            # Get current time
            current_time = dt.now()
            
            # If the scheduled time is in the past, start immediately
            if start_datetime <= current_time:
                print("The scheduled time is already past. Starting immediately.")
                return
            
            # Calculate the time difference
            time_difference = (start_datetime - current_time).total_seconds()
            print(f"Bot will start at {start_datetime}. Waiting {time_difference:.1f} seconds...")
            
            # Wait until the specified time
            await asyncio.sleep(time_difference)
            print("Starting the bot now!")
            
        except Exception as e:
            print(f"Error in scheduling: {e}")
            print("Starting immediately instead.")


    async def monitor_ticks(self):
        """Monitor incoming tick data and process for trading decisions"""
        print("Starting tick monitoring...")
        
        try:
            while self.running:
                # Check for new tick data
                try:
                    response = await asyncio.wait_for(self.ws_data.recv(), timeout=1.0)
                    data = json.loads(response)
                    
                    if data.get("msg_type") == "tick":
                        # Extract tick data
                        quote = float(data["tick"]["quote"])
                        pip_size = int(data["tick"]["pip_size"])
                        formatted_quote = f"{quote:.{pip_size}f}"
                        self.last_digit = int(formatted_quote[-1])
                        # Print tick with timestamp
                        current_time = dt.now().strftime("%H:%M:%S")
                        self.prev_digits.append(self.last_digit)

                        even_or_odd = self.last_digit % 2

                        if len(self.last_digits_amount) < self.digit_count:
                            self.last_digits_amount.append(even_or_odd)
                        else:
                            self.last_digits_amount.pop(0)
                            self.last_digits_amount.append(even_or_odd)

                        if len(self.prev_digits) > 25:
                            self.prev_digits.pop(0)


                        # Convert list of 0s and 1s to a string like "10101..."
                        self.digit_string = ''.join(str(d) for d in self.last_digits_amount)
                        print(f"Last {self.digit_count} digits as a string: {self.digit_string}")

                        # this will execute when the trade is opened
                        if self.trade_open and (self.resetor != 100):
                            
                            print(f"[{current_time}] Last Digit is: {self.last_digit}")
                            
                            if self.trade_type == 0:
                                if self.last_digit > int(self.over_digit) or self.last_digit < int(self.under_digit):
                                    self.result = "win"
                                else:
                                    self.result = "loss"
                            
                            elif self.trade_type == 1:
                                comparing_bit = (self.last_digit % 2)
                                print(comparing_bit)
                                print(self.checking_bit)
                                if comparing_bit == self.checking_bit:
                                    self.result = "win"
                                else:
                                    self.result = "loss"
                        
                            await self.check_contract_result()
                            self.trade_open = False
                       
                        print(f"[{current_time}] Last Digit: {self.last_digit}")
                        
                        # Process the digit for trading only if not in active trade
                        if not self.trade_active and self.not_first_time_recovary:
                            await self.process_digit(self.last_digit)
                        elif not self.trade_active:
                            await self.process_digit(self.last_digit)
                            
                except asyncio.TimeoutError:
                    # No new tick data, continue monitoring
                    continue
                    
                except Exception as e:
                    print(f"Error processing tick: {e}")
                    await asyncio.sleep(0.5)
                    continue

        except asyncio.CancelledError:
            print("Tick monitoring cancelled")
        except Exception as e:
            print(f"Error monitoring ticks: {e}")


    async def process_digit(self, digit):
        """Process each incoming digit according to trading strategy"""
        if self.trade_active:
            return  # Skip if a trade is already in progress
        
        if True:
            # In normal mode, we wait for our target digit
            if self.first_time_2 == True:
                count = 0
                if self.trade_type == 0:
                    for i in self.prev_digits[-self.prev_digits_count:]:
                        for j in self.numbers_list:
                            if i == j:
                                count += 1
                                break

                elif self.trade_type == 1 and self.step_one:
                    is_even = self.current_target_digit % 2 == 0
                    self.precentage = 0
                    for i in self.digit_string:
                        if int(i) % 2 == 0:
                            self.precentage += 1
                    self.precentage /= self.digit_count

                    if self.precentage <= (1 - self.acceptable_precentage) and (self.digit_string[-2:] == "11"):
                        is_even = True
                        self.start = True
                    elif self.precentage >= self.acceptable_precentage and (self.digit_string[-2:] == "00"):
                        is_even = False
                        self.start = True
                
                    # Create the recovery sequence
                    if is_even:
                        # If analysis number is even, start with odd sequence
                        self.recovery_sequence = self.recovery_sequence_even  # odd odd even odd odd even...
                    else:
                        # If analysis number is odd, start with even sequence
                        self.recovery_sequence = self.recovery_sequence_odd  # even even odd even even odd...
                

                elif self.trade_type == 1 and (not self.step_one):
                    for i in self.prev_digits[-self.prev_digits_count:]:
                        for j in self.numbers_list:
                            if i == j:
                                count += 1
                                break

                if len(self.prev_digits) < self.digit_count:
                    is_even = False
                    self.start = False

                if self.waiting_for_target and self.start \
                and len(self.prev_digits) >= self.digit_count and \
                    self.trade_type == 1 and self.step_one:
                    print(f"✓ Target Precentage found! for Recovery Trade .... Placing trade...")
                    self.waiting_for_target = False
                    self.trade_active = True
                    self.trade_open = True
                    
                    # Place trade
                    await self.place_trade()
                    
                    # After wait time, we'll start looking for the next digit
                    self.waiting_for_target = True
                    self.trade_active = False
                    time_difference = 0
                    self.first_time_2 = False
                    self.step_one = False
                    self.start = False

                elif self.waiting_for_target and count == self.prev_digits_count \
                    and len(self.prev_digits) >= self.prev_digits_count and self.trade_type == 0:
                    print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count:]} found! Placing trade...")
                    self.waiting_for_target = False
                    self.trade_active = True
                    self.trade_open = True
                    
                    # Place trade
                    await self.place_trade()
                    
                    # After wait time, we'll start looking for the next digit
                    self.waiting_for_target = True
                    self.trade_active = False
                    time_difference = 0
                    self.first_time_2 = False

                elif self.waiting_for_target and count == self.prev_digits_count \
                    and len(self.prev_digits) >= self.prev_digits_count and \
                    self.trade_type == 1 and (not self.step_one):
                    print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count:]} found! for Recovery Trade Placing trade...")
                    self.waiting_for_target = False
                    self.trade_active = True
                    self.trade_open = True
                    
                    # Place trade
                    await self.place_trade()
                    
                    # After wait time, we'll start looking for the next digit
                    self.waiting_for_target = True
                    self.trade_active = False
                    time_difference = 0
                    self.first_time_2 = False
                    self.step_one = False
                    

            else:
                if self.resetor != 100:
                    if self.result == "loss":
                        waiting_time = self.wait_time_loss 
                    else:
                        waiting_time = self.wait_time_win
                    time_now = dt.now().strftime("%H:%M:%S")
                    time_now_obj = dt.strptime(time_now, "%H:%M:%S")
                    trade_time_obj = dt.strptime(self.trade_time_indicator, "%H:%M:%S")
                    time_difference = (time_now_obj - trade_time_obj).total_seconds()

                    # This indicator for separate the patterns as the last digit state
                    self.resetor = -100

                    if time_difference < waiting_time:
                        print(f"[{time_now}] ........ Waiting for Trade ({waiting_time - time_difference:.1f} seconds)")
                    
                    if time_difference >= waiting_time:
                        count = 0
                        if self.trade_type == 0:
                            for i in self.prev_digits[-self.prev_digits_count:]:
                                for j in self.numbers_list:
                                    if i == j:
                                        count += 1
                                        break

                        elif self.trade_type == 1 and self.step_one:
                            is_even = self.current_target_digit % 2 == 0
                            self.precentage = 0
                            for i in self.digit_string:
                                if int(i) % 2 == 0:
                                    self.precentage += 1
                            self.precentage /= self.digit_count

                            if self.precentage <= (1 - self.acceptable_precentage) and (self.digit_string[-2:] == "11"):
                                is_even = True
                                self.start = True
                            elif self.precentage >= self.acceptable_precentage and (self.digit_string[-2:] == "00"):
                                is_even = False
                                self.start = True

                        
                            # Create the recovery sequence
                            if is_even:
                                # If analysis number is even, start with odd sequence
                                self.recovery_sequence = self.recovery_sequence_even  # odd odd even odd odd even...
                            else:
                                # If analysis number is odd, start with even sequence
                                self.recovery_sequence = self.recovery_sequence_odd  # even even odd even even odd...
                        
                        
                        elif self.trade_type == 1 and (not self.step_one):
                            for i in self.prev_digits[-self.prev_digits_count:]:
                                for j in self.numbers_list:
                                    if i == j:
                                        count += 1
                                        break

                        if len(self.prev_digits) < self.digit_count:
                            is_even = False
                            self.start = False

                        if self.waiting_for_target and self.start \
                        and len(self.prev_digits) >= self.digit_count and \
                            self.trade_type == 1 and self.step_one:
                            print(f"✓ Target Precentage found! for Recovery Trade ... Placing trade...")
                            self.waiting_for_target = False
                            self.trade_active = True
                            self.trade_open = True
                            
                            # Place trade
                            await self.place_trade()
                            
                            # After wait time, we'll start looking for the next digit
                            self.waiting_for_target = True
                            self.trade_active = False
                            time_difference = 0
                            self.first_time_2 = False
                            self.step_one = False
                            self.start = False

                        elif self.waiting_for_target and count == self.prev_digits_count \
                        and len(self.prev_digits) >= self.prev_digits_count and self.trade_type == 0:
                            print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count:]} found! Placing trade...")
                            self.waiting_for_target = False
                            self.trade_active = True
                            self.trade_open = True
                            
                            # Place trade
                            await self.place_trade()
                            
                            # After wait time, we'll start looking for the next digit
                            self.waiting_for_target = True
                            self.trade_active = False
                            time_difference = 0

                        elif self.waiting_for_target and count == self.prev_digits_count \
                        and len(self.prev_digits) >= self.prev_digits_count and \
                            self.trade_type == 1 and (not self.step_one):
                            print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count:]} found! for Recovery Trade .. Placing trade...")
                            self.waiting_for_target = False
                            self.trade_active = True
                            self.trade_open = True
                            
                            # Place trade
                            await self.place_trade()
                            
                            # After wait time, we'll start looking for the next digit
                            self.waiting_for_target = True
                            self.trade_active = False
                            time_difference = 0
                            self.first_time_2 = False
                            self.step_one = False


    async def place_trade(self, target_type=None):
        """Place a trade with the specified parameters - Optimized for speed"""
        try:
            # Round stake to 2 decimal places
            stake = round(self.current_stake, 2)
            
            # Determine contract types and barriers based on trade type
            if self.trade_type == 0:
                # Normal mode - over/under trades
                contract_types = ["DIGITOVER", "DIGITUNDER"]
                barriers = [str(self.over_digit), str(self.under_digit)]
                websockets = [self.ws_trading, self.ws_trading_2]
            else:
                # Recovery mode - odd/even trades
                if self.recovery_sequence[self.martingale_stake_number - 1] == 1:
                    contract_type = "DIGITODD"
                    self.checking_bit = 1
                else:
                    contract_type = "DIGITEVEN"
                    self.checking_bit = 0
                
                contract_types = [contract_type, contract_type]
                barriers = [None, None]  # No barriers for odd/even
                websockets = [self.ws_trading, self.ws_trading_2]
            
            # Create proposal messages
            proposal_msgs = []
            for i, (contract_type, barrier) in enumerate(zip(contract_types, barriers)):
                msg = {
                    "proposal": 1,
                    "amount": stake,
                    "basis": "stake",
                    "contract_type": contract_type,
                    "currency": self.currency,
                    "duration": 1,
                    "duration_unit": "t",
                    "symbol": self.config["symbol"]
                }
                if barrier is not None:
                    msg["barrier"] = barrier
                proposal_msgs.append(msg)
            
            # Send both proposals concurrently
            proposal_tasks = [
                self.send_and_receive(websockets[i], proposal_msgs[i])
                for i in range(len(proposal_msgs))
            ]
            
            # Wait for both proposals to complete
            proposal_results = await asyncio.gather(*proposal_tasks, return_exceptions=True)
            
            # Extract contract IDs
            contract_ids = []
            for i, result in enumerate(proposal_results):
                if isinstance(result, Exception):
                    print(f"Proposal {i} failed: {result}")
                    continue
                
                if not result or "proposal" not in result:
                    print(f"Failed to get proposal for {contract_types[i]}")
                    continue
                
                proposal = result["proposal"]
                if isinstance(proposal, list):
                    proposal = proposal[0]  # Take first if it's a list
                
                contract_id = proposal.get("id")
                if contract_id:
                    contract_ids.append((contract_id, websockets[i], contract_types[i], barriers[i]))
            
            if not contract_ids:
                print("No valid proposals received")
                self.trade_active = False
                return "error"
            
            # Create buy orders for all valid contracts concurrently
            buy_tasks = [
                self.send_and_receive(ws, {
                    "buy": contract_id,
                    "price": stake,
                })
                for contract_id, ws, _, _ in contract_ids
            ]
            
            # Execute all buy orders simultaneously
            buy_results = await asyncio.gather(*buy_tasks, return_exceptions=True)
            
            # Process buy results
            successful_buys = []
            for i, result in enumerate(buy_results):
                contract_id, ws, contract_type, barrier = contract_ids[i]
                
                if isinstance(result, Exception):
                    print(f"Buy order {i} failed: {result}")
                    continue
                
                if result and "buy" in result:
                    successful_buys.append(result)
                    barrier_text = f" {barrier}" if barrier else ""
                    print(f"Placed {contract_type}{barrier_text} trade: ${stake}")
                else:
                    print(f"Buy request failed for {contract_type}")
            
            if not successful_buys:
                print("All buy requests failed")
                self.trade_active = False
                return "error"
            
            # Record trade time
            trade_time = dt.now().strftime("%H:%M:%S")
            self.trade_time_indicator = trade_time
            
            # Log successful trades
            if self.trade_type == 0:
                print(f"[{trade_time}] Placed both DIGITOVER {self.over_digit} and DIGITUNDER {self.under_digit} trades: ${stake} each")
            else:
                contract_name = "DIGITODD" if self.checking_bit == 1 else "DIGITEVEN"
                print(f"[{trade_time}] Placed both {contract_name} trades: ${stake} each")
            
            # Calculate profits based on first successful trade
            first_buy = successful_buys[0]
            payout = first_buy["buy"].get("payout", 0)
            
            if self.trade_type == 0:
                # Normal mode - two different contracts
                self.profit_plus = payout - (stake * 2)
                self.profit_minus = stake
            else:
                # Recovery mode - same contract type
                self.profit_plus = (payout - stake) * 2
                self.profit_minus = stake
            
            if self.in_recovery_mode:
                self.trade_open = True
                
            return "success"
            
        except Exception as e:
            print(f"Error in place_trade: {e}")
            self.trade_active = False
            return "error"


    async def check_contract_result(self):
            outcome = self.result
            
            # Process result
            if outcome == "win" and self.trade_type == 0:
                print(f"✓ Trade WON")
                self.current_stake = self.base_stake 
                profit = self.profit_plus
                self.step_one = True

            elif outcome == "loss":
                print(f"✗ Trade LOST")
                self.resetor = 0
                self.trade_type = 1
                profit = -self.profit_minus

            elif outcome == "win" and self.trade_type == 1:
                print(f"✓ Trade WON")       
                self.recovery_win_count += self.martingale_weight_list[self.martingale_stake_number - 1]
                profit = self.profit_plus
                


            await self.check_account_status(profit)

            # Check take profit and stop loss
            if self.total_profit >= self.config["take_profit"]:
                print(f"✓ TAKE PROFIT REACHED! ${self.total_profit:+.2f}")
                await self.ask_to_continue()
                
            if self.total_profit <= -self.config["stop_loss"]:
                print(f"✗ STOP LOSS TRIGGERED! ${self.total_profit:+.2f}")
                await self.ask_to_continue()


    
    async def check_account_status(self, profit):
        """Get current account balance and status with proper synchronization"""
        try:
            if profit > 0:
                profit_change = profit
            else:
                profit_change = profit * 2
            self.balance_after = self.balance_previous + profit_change
            old_balance = self.balance_previous
            new_balance = self.balance_after
    
            self.current_balance = new_balance
            
            self.total_profit = self.current_balance - self.initial_balance

            self.balance_previous = self.balance_after            

            if self.trade_type == 0:
                print(f"Balance: ${self.current_balance:.2f} | " +
                    f"Change: ${(profit_change):+.2f} | " +
                    f"P/L: ${self.total_profit:+.2f} | ")
            
            elif self.trade_type == 1:
                if self.first_time_1:
                    self.amount_revcovered += (profit_change + self.profit_sum)
                    print(f"Balance: ${self.current_balance:.2f} | " +
                        f"Change: ${(profit_change + self.profit_sum):+.2f} | " +
                        f"P/L: ${self.total_profit:+.2f} | " +
                        f"Recovered Amount: ${(self.amount_revcovered):+.2f} need to reach --> 0 ")
                    self.first_time_1 = False
                else:
                    self.amount_revcovered += (profit_change + self.profit_sum)
                    print(f"Balance: ${self.current_balance:.2f} | " +
                        f"Change: ${(profit_change + self.profit_sum):+.2f} | " +
                        f"P/L: ${self.total_profit:+.2f} | " +
                        f"Recovered Amount: ${self.amount_revcovered:+.2f} need to reach --> 0 ")

                if profit_change > 0 and self.amount_revcovered < 0 and self.recovery_win_count >= 3: 
                    self.current_stake = self.martingale_stake_list[0]
                    self.recovery_win_count = 0
                    self.martingale_stake_number = 1
                    self.step_one = True
                    print("✓ Mid win successful! Returning again to recovery mode.")

                elif self.amount_revcovered >= 0:
                    self.trade_type = 0
                    self.current_stake = self.base_stake
                    self.amount_revcovered = 0
                    self.recovery_win_count = 0
                    self.martingale_stake_number = 0
                    self.step_one = True
                    print("✓ Recovery successful! Returning to normal mode.")

                else:
                    if self.current_stake == self.base_stake:
                        self.current_stake = self.martingale_stake_list[0]
                        self.recovery_win_count = 0
                        self.martingale_stake_number = 1
                    else:
                        if profit_change > 0:
                            if self.martingale_stake_number == len(self.martingale_stake_list):
                                print(f"✗ MAX MARTINGALE STAKE AMOUNT REACHED! --> P/L:${self.total_profit:+.2f}")
                                await self.ask_to_continue()

                            self.current_stake = self.martingale_stake_list[self.martingale_stake_number]
                            if self.max_stake < self.current_stake:
                                self.current_stake = self.max_stake
                            
                            if self.max_stake == self.current_stake:
                                print(f"✗ MAX STAKE REACHED! --> P/L:${self.total_profit:+.2f}")
                                await self.ask_to_continue()
                            
                            self.martingale_stake_number += 1

                        else:
                            self.recovery_win_count -= self.martingale_weight_list[self.martingale_stake_number - 1]

                            if self.martingale_stake_number == len(self.martingale_stake_list):
                                print(f"✗ MAX MARTINGALE STAKE AMOUNT REACHED! --> P/L:${self.total_profit:+.2f}")
                                await self.ask_to_continue()

                            self.current_stake = self.martingale_stake_list[self.martingale_stake_number]
                            if self.max_stake < self.current_stake:
                                self.current_stake = self.max_stake
                            
                            if self.max_stake == self.current_stake:
                                print(f"✗ MAX STAKE REACHED! --> P/L:${self.total_profit:+.2f}")
                                await self.ask_to_continue()

                            self.martingale_stake_number += 1
            
            
            # Add trade to history
            trade_time = dt.now().strftime("%H:%M:%S")
            self.trades_history.append({
                "time": trade_time,
                "stake": self.current_stake,
                "profit_change": profit_change,
                "last_digit": self.last_digit,
                "target_digit": self.current_target_digit,
                "in_recovery": self.in_recovery_mode,
                "balance": self.current_balance
            })
            return True
            
        except Exception as e:
            print(f"Error checking account status: {e}")
            return False
    
    async def ask_to_continue(self):
        """Ask user if they want to continue trading after TP/SL hit"""
        self.running = False  # Stop the bot temporarily
        
        print("\n" + "="*50)
        print("BOT STOPPED - PROFIT TARGET OR STOP LOSS OR MAX STAKE REACHED")
        print("="*50)
        
        # Loop until we get a valid response
        while True:
            self.tick_monitor_1.cancel()
            user_input = input("Do you want to continue trading? (y/n): ").strip().lower()
            
            if user_input == 'y':
                # Reset profit counters and continue
                self.initial_balance = self.current_balance
                self.total_profit = 0
                self.current_stake = self.base_stake  # Reset stake
                self.in_recovery_mode = False  # Exit recovery mode if active
                self.trade_active = False
                self.running = True  # Resume the bot
                self.first_time_2 = True
                
                print("\n" + "="*50)
                print("BOT RESTARTED")
                print("="*50 + "\n")
                # Get configuration from user
                config = await get_user_config()
                
                # Create and run the bot
                bot = DigitBot(config)
                await bot.run()
                break
            elif user_input == 'n':
                print("Bot session ended. Thank you for using Digit Bot!")
                # Keep running = False to exit main loop
                break
            else:
                print("Invalid input. Please enter 'y' for yes or 'n' for no.")

    async def run(self):
        """Main bot execution loop"""
        # Wait until configured start time
        await self.wait_until_start_time()
        
        # Connect all WebSockets
        connected = await self.connect_websockets()
        if not connected:
            print("Failed to connect or authenticate. Exiting.")
            return
            
        #trade_type = "Over 5" if self.config["is_over"] else "Under 4"
        #self.barrier = self.config["barrier"]
        
        print("\n" + "="*50)
        print("BOT STARTED")
        print(f"Trading {self.config['symbol']} with Over Under strategy:")
        print(f"  - Initial stake ${self.base_stake}")
        print("="*50 + "\n")
        
        # Start tick monitoring
        tick_monitor = asyncio.create_task(self.monitor_ticks())
        self.tick_monitor_1 = tick_monitor
        try:
            # Wait for tick monitor to complete or bot to be stopped
            while self.running:
                await asyncio.sleep(0.5)
                
            # Cancel the tick monitor if we're no longer running
            if not self.running:
                tick_monitor.cancel()

                
        except asyncio.CancelledError:
            print("Bot operation cancelled")
        except Exception as e:
            print(f"Error in main execution: {e}")
        finally:
            # Close all WebSocket connections
            for ws in [self.ws_data, self.ws_trading]:
                if ws:
                    await ws.close()
            print("Bot stopped. All connections closed.")

async def get_user_config():
    """Get user configuration with input validation"""
    
    print("\n" + "="*50)
    print("DIGIT BOT CONFIGURATION")
    print("="*50)
    
    # Determine if we're trading Over or Under
    #while True:
        #trade_type = input("Enter the over/under (over/under): ").strip().lower()
        #if trade_type in ["over", "under"]:
            #is_over = (trade_type == "over")
            #break
        #else:
            #print("Invalid input. Please enter 'over' or 'under'.")
    #s_over = True
    #trade_type = "over"
    # Get barrier based on Over/Under selection
   

    # Get API token
    api_token = input(f"Enter API Token for bot: ").strip()
    while not api_token:
        print("Error: API token cannot be empty.")
        api_token = input(f"Enter API Token for bot: ").strip()
    
    # Get symbol with default
    symbol = input("Enter symbol (default: R_10): ") or "R_10"
    
    while True:
        try:
            barrier_over = int(input(f"Contract number over (default: 5): ") or (5))
            break
        except ValueError:
            print("Invalid input. Please enter a valid number.")

    #trade_type = "over"
    # Get barrier based on Over/Under selection
    while True:
        try:
            barrier_under = int(input(f"Contract number under (default: 4): ") or (4))
            break
        except ValueError:
            print("Invalid input. Please enter a valid number.")

    
    # Validate numbers list input (positive numbers separated by spaces)
    while True:
        try:
            user_input = input("Enter numbers list (default: 0 1 2 3 4 5 6 7 8 9): ") or "0 1 2 3 4 5 6 7 8 9"
            numbers_list = user_input.split()
            
            # Convert strings to floats and validate
            numbers_list = [int(num) for num in numbers_list]
            
            # Check if all numbers are positive
            if any(num < 0 for num in numbers_list):
                raise ValueError("All numbers must be positive.")
                
            # Check if list is not empty
            if len(numbers_list) == 0:
                raise ValueError("Numbers list cannot be empty.")
                
            print(f"Numbers list: {numbers_list}")
            break
            
        except ValueError as e:
            if "could not convert" in str(e):
                print("Invalid input: Please enter valid numbers separated by spaces.")
            else:
                print(f"Invalid input: {e}. Please enter valid positive numbers.")
        except Exception as e:
            print(f"Invalid input: {e}. Please enter numbers separated by spaces.")


    # Previous digit count to be considered
    while True:
        try:
            prev_digits_count = int(input(f"Previous digit count (default: 3): ") or (3))
            break
        except ValueError:
            print("Invalid input. Please enter a valid integer number.")

    def get_binary_sequence(prompt):
        while True:
            sequence = input(prompt).strip()
            if not sequence:
                print("Error: Sequence cannot be empty.")
            elif not set(sequence).issubset({'0', '1'}):
                print("Error: Sequence must contain only 0s and 1s.")
            else:
                return [int(char) for char in sequence]

    # Get Even sequence
    even_sequence = "1111111" #get_binary_sequence(f"Enter Recovery sequence for ODD-EVEN Recovary Trades: ")
    print(f"EVEN Sequence: {even_sequence} with length: {len(even_sequence)}")

    # Get Odd sequence
    odd_sequence = "0000000" #get_binary_sequence(f"Enter ODD sequence for ODD-EVEN Recovary Trades: ")
    print(f"ODD Sequence: {odd_sequence} with length: {len(odd_sequence)}")


    # Validate normal stake input (positive float)
    while True:
        try:
            normal_stake = float(input("Enter normal stake amount (default: 1.0): ") or 1.0)
            if normal_stake <= 0:
                raise ValueError("Stake must be a positive number for normal stake.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake for normal stake.")

    
    # Validate Max stake input (positive float)
    while True:
        try:
            max_stake = float(input("Enter MAX stake amount (default: 50.0): ") or 50.0)
            if max_stake <= 0:
                raise ValueError("Stake must be a positive number for MAX stake.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake for MAX stake.")

    
    # Validate Martingale stake input (positive float)
    while True:
        try:
            martingale_stake = float(input("Enter Martingale Stake (default: 0.4): ") or 0.4)
            if martingale_stake <= 0:
                raise ValueError("Stake must be a positive number for martingale stake.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake for martingale stake.")
    
    # Validate martingale_factor (positive float)
    while True:
        try:
            #martingale_factor = float(input("Enter Martingale Factor (default: 7.8): ") or 7.8)
            martingale_factor = 7.8
            if martingale_factor <= 0:
                raise ValueError("Martingale Factor must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid Martingale Factor.")
    
    # Validate martingale_split (positive float)
    while True:
        try:
            #martingale_split = float(input("Enter martingale split (default: 1): ") or 1)
            martingale_split = 2
            if martingale_split <= 0:
                raise ValueError("Martingale split must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid martingale split.")

    # Get start time
    current_time = dt.now()
    default_start_time = (current_time + datetime.timedelta(minutes=1)).strftime("%d-%m-%Y %H:%M:%S")
    
    while True:
        try:
            start_time = input(f"Enter start time (format: DD-MM-YYYY HH:MM:SS, default: {default_start_time}): ") or default_start_time
            # Validate format
            dt.strptime(start_time, "%d-%m-%Y %H:%M:%S")
            break
        except ValueError:
            print("Invalid date format. Please use DD-MM-YYYY HH:MM:SS")
    
    # Validate wait_time After a win (positive integer)
    while True:
        try:
            wait_time = int(input("Enter waiting seconds after a win trade (default: 0): ") or 0)
            if wait_time < 0:
                raise ValueError("Win Waiting Time must be a non-negative integer.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid number.")

    # Validate wait_time After a loss (positive integer)
    while True:
        try:
            wait_time_loss = int(input("Enter waiting seconds after a loss trade (default: 0): ") or 0)
            if wait_time < 0:
                raise ValueError("Loss Waiting Time must be a non-negative integer.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid number.")


    # Validate take_profit (positive float)
    while True:
        try:
            take_profit = float(input("Enter take profit amount (default: 10): ") or 10)
            if take_profit <= 0:
                raise ValueError("Take profit must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid take profit.")

    # Validate stop_loss (positive float)
    while True:
        try:
            stop_loss = float(input("Enter stop loss amount (default: 200): ") or 200)
            if stop_loss <= 0:
                raise ValueError("Stop loss must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stop loss.")

    return {
        "symbol": symbol,
        "api_token": api_token,
        "app_id": "66229",       # Application ID for Deriv API
        "stake": normal_stake,
        "max_stake": max_stake,
        #"is_over": is_over,
        "barrier_over": barrier_over,
        "barrier_under": barrier_under,
        "numbers_list": numbers_list,
        "martingale_split": martingale_split,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "martingale_factor": martingale_factor,
        "wait_time": wait_time,
        "start_time": start_time,
        "martingale_stake": martingale_stake,
        "wait_time_loss": wait_time_loss,
        "prev_digits_count": prev_digits_count,
        "even_sequence": even_sequence,
        "odd_sequence": odd_sequence

    }

async def main():
    """Main entry point for the application"""
    print("====== DIGIT TRADING BOT ======")
    print("This bot places Over 5 trades based on sequential digit analysis with odd/even recovery")
    
    try:
        # Get configuration from user
        config = await get_user_config()
        
        # Create and run the bot
        bot = DigitBot(config)
        await bot.run()
        
    except KeyboardInterrupt:
        print("\nProgram terminated by user")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        print("\nBot execution ended")

if __name__ == "__main__":
    # Run the main coroutine
    asyncio.run(main())

