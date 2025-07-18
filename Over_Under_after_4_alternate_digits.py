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
        self.wait_time_loss = config["wait_time_loss"]
        self.wait_time_win = config["wait_time"]
        
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
        self.result = ""
        self.first_time_2 = True
        self.trade_type = 0
        self.trade_time_indicator = None
        self.profit_plus = 0
        self.profit_minus = 0
        self.trade_open = False
        self.not_first_time_recovary = False
        self.tick_monitor_1 = None
        self.prev_digits = []
        self.over_digit = self.config["barrier_over"]
        self.under_digit = self.config["barrier_under"]
        self.alter_pattern_length = config["alter_pattern_length"]
        self.current_barrier = None
        self.prev_digits_count = self.config["prev_digits_count"]
        
        self.trade_type_contract = None
        self.step_one = True
        self.prev_digits_count_trade_type_1 = self.prev_digits_count
        self.numbers_list_trade_type_1 = [0, 1, 2, 3, 4, 5]
        self.numbers_list = self.numbers_list_trade_type_1
        self.numbers_list_trade_type_2 = [4, 5, 6, 7, 8, 9]

        self.normal_stake = self.config["normal_stake"]
        self.current_stake = self.normal_stake
        
        self.martingale__trade_number = 0
        self.martingale_started = False
        self.even_trades = 0
        self.appvoed_under = False
        self.appvoed_over = False


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

                        # this will execute when the trade is opened
                        if self.trade_open:
                            
                            print(f"[{current_time}] Last Digit is: {self.last_digit}")
                            
                            if self.trade_type == 0:
                                if self.trade_type_contract == "over":
                                    if self.last_digit > int(self.current_barrier):
                                        self.result = "win"
                                    else:
                                        self.result = "loss"
                                elif self.trade_type_contract == "under":
                                    if self.last_digit < int(self.current_barrier):
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
    
        # In normal mode, we wait for our target digit
        if self.first_time_2 == True:
            count_under = 0
            count_over = 0
            count_pattern = 0
            if self.trade_type == 0:
                self.appvoed_under = False
                self.appvoed_over = False

                for i in self.prev_digits[-(self.prev_digits_count + self.alter_pattern_length): -self.alter_pattern_length]:
                    for j in self.numbers_list_trade_type_1:
                        if i == j:
                            count_under += 1
                            break
                
                for i in self.prev_digits[-(self.prev_digits_count + self.alter_pattern_length): -self.alter_pattern_length]:
                    for j in self.numbers_list_trade_type_2:
                        if i == j:
                            count_over += 1
                            break

                if count_under == self.prev_digits_count_trade_type_1:
                    for i in range(self.alter_pattern_length):
                        if i % 2 == 0:
                            count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_2) else 0)
                        else:
                            count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_1) else 0)

                    if self.alter_pattern_length % 2 == 0:
                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_under = True
                    else:
                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_over = True

                elif count_over == self.prev_digits_count_trade_type_1:
                    for i in range(self.alter_pattern_length):
                        if i % 2 == 0:
                            count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_1) else 0)
                        else:
                            count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_2) else 0)

                    if self.alter_pattern_length % 2 == 0:
                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_over = True
                    else:
                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_under = True


            if self.waiting_for_target and self.appvoed_over\
                and len(self.prev_digits) >= self.prev_digits_count_trade_type_1:
    
                print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count_trade_type_1:]} found! Placing a Trade...")
                self.waiting_for_target = False
                self.trade_active = True
                self.trade_open = True
                self.appvoed_over = False
                self.trade_type_contract = "over"

                self.even_trades += 1
                print(f"Trade number: {self.even_trades}")

                # Place trade
                await self.place_trade()
                
                # After wait time, we'll start looking for the next digit
                self.waiting_for_target = True
                self.trade_active = False
                time_difference = 0
                self.first_time_2 = False

            
            elif self.waiting_for_target and self.appvoed_under\
                and len(self.prev_digits) >= self.prev_digits_count_trade_type_1:
               
                print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count_trade_type_1:]} found! Placing a Trade...")
                self.waiting_for_target = False
                self.trade_active = True
                self.trade_open = True
                self.appvoed_under = False
                self.trade_type_contract = "under"
                
                self.even_trades += 1
                print(f"Trade number: {self.even_trades}")

                # Place trade
                await self.place_trade()
                
                # After wait time, we'll start looking for the next digit
                self.waiting_for_target = True
                self.trade_active = False
                time_difference = 0
                self.first_time_2 = False

        else:
            if self.result == "loss":
                waiting_time = self.wait_time_loss 
            else:
                waiting_time = self.wait_time_win
            time_now = dt.now().strftime("%H:%M:%S")
            time_now_obj = dt.strptime(time_now, "%H:%M:%S")
            trade_time_obj = dt.strptime(self.trade_time_indicator, "%H:%M:%S")
            time_difference = (time_now_obj - trade_time_obj).total_seconds()

            if time_difference < waiting_time:
                print(f"[{time_now}] ........ Waiting for Trade ({waiting_time - time_difference:.1f} seconds)")
            
            if time_difference >= waiting_time:
                count_under = 0
                count_over = 0
                count_pattern = 0

                if self.trade_type == 0:
                    self.appvoed_under = False
                    self.appvoed_over = False

                    for i in self.prev_digits[-(self.prev_digits_count + self.alter_pattern_length): -self.alter_pattern_length]:
                        for j in self.numbers_list_trade_type_1:
                            if i == j:
                                count_under += 1
                                break
                    
                    for i in self.prev_digits[-(self.prev_digits_count + self.alter_pattern_length): -self.alter_pattern_length]:
                        for j in self.numbers_list_trade_type_2:
                            if i == j:
                                count_over += 1
                                break
                    
                    if count_under == self.prev_digits_count_trade_type_1:
                        for i in range(self.alter_pattern_length):
                            if i % 2 == 0:
                                count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_2) else 0)
                            else:
                                count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_1) else 0)

                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_under = True


                    elif count_over == self.prev_digits_count_trade_type_1:
                        for i in range(self.alter_pattern_length):
                            if i % 2 == 0:
                                count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_1) else 0)
                            else:
                                count_pattern += (1 if (self.prev_digits[-self.alter_pattern_length + i] in self.numbers_list_trade_type_2) else 0)

                        if count_pattern == self.alter_pattern_length:
                            self.appvoed_over = True


                if self.waiting_for_target and self.appvoed_over\
                    and len(self.prev_digits) >= self.prev_digits_count_trade_type_1:
                    
                    print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count_trade_type_1:]} found! Placing a Trade...")
                    self.waiting_for_target = False
                    self.trade_active = True
                    self.trade_open = True
                    self.appvoed_over = False
                    self.trade_type_contract = "over"
                    
                    self.even_trades += 1
                    print(f"Trade number: {self.even_trades}")

                    # Place trade
                    await self.place_trade()
                    
                    # After wait time, we'll start looking for the next digit
                    self.waiting_for_target = True
                    self.trade_active = False
                    time_difference = 0
                    self.first_time_2 = False
                    self.step_one = False

                
                elif self.waiting_for_target and self.appvoed_under \
                    and len(self.prev_digits) >= self.prev_digits_count_trade_type_1:
                  
                    print(f"✓ Target pattern {self.prev_digits[-self.prev_digits_count_trade_type_1:]} found! Placing a Trade...")
                    self.waiting_for_target = False
                    self.trade_active = True
                    self.trade_open = True
                    self.appvoed_under = False
                    self.trade_type_contract = "under"
                    
                    self.even_trades += 1
                    print(f"Trade number: {self.even_trades}")

                    # Place trade
                    await self.place_trade()
                    
                    # After wait time, we'll start looking for the next digit
                    self.waiting_for_target = True
                    self.trade_active = False
                    time_difference = 0
                    self.first_time_2 = False
                    self.step_one = False


    async def place_trade(self, target_type=None):
        """Place a trade with the specified parameters"""
        try:
            # Round stake to 2 decimal places
            stake = round(self.current_stake, 2)
            
            
            # Regular mode using over/under - both contracts in single proposal
            if self.trade_type_contract == "over":
                contract_type_over = "DIGITOVER"
                barrier = str(self.over_digit)
                self.current_barrier = barrier
            else:
                contract_type_over = "DIGITUNDER"
                barrier = str(self.under_digit)
                self.current_barrier = barrier
            
            # Create proposal message for both contracts
            proposal_msg = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type_over,
                "barrier": barrier,
                "currency": self.currency,
                "duration": 1,
                "duration_unit": "t",  # t = tick
                "symbol": self.config["symbol"]
            }
            
            # Get proposal for both contracts
            proposal = await self.send_and_receive(self.ws_trading, proposal_msg)
            if not proposal or "proposal" not in proposal:
                print(f"Failed to get proposal for {contract_type_over}")
                self.trade_active = False
                return "error"
            
            # Handle both proposals (should return array of proposals)
            proposals = proposal["proposal"] if isinstance(proposal["proposal"], list) else [proposal["proposal"]]
            
            contract_ids = []
            buy_responses = []
            
            # Extract contract IDs and place buy orders for both
            for i, prop in enumerate(proposals):
                contract_id = prop.get("id")
                if not contract_id:
                    print(f"No contract ID received for {contract_type_over[i]}")
                    continue
                    
                # Buy contract
                buy_response = await self.send_and_receive(self.ws_trading, {
                    "buy": contract_id,
                    "price": stake,
                })
                
                if buy_response and "buy" in buy_response:
                    contract_ids.append(buy_response["buy"].get("contract_id"))
                    buy_responses.append(buy_response)
                    print(f"Placed {contract_type_over} {barrier} trade: ${stake}")
                else:
                    print(f"Buy request failed for {contract_type_over} {barrier}")
            
            # Check if at least one trade was successful
            if not buy_responses:
                print("All buy requests failed")
                self.trade_active = False
                return "error"
            
            trade_time = dt.now().strftime("%H:%M:%S")
            self.trade_time_indicator = trade_time
            print(f"[{trade_time}] Placed {contract_type_over} {barrier} trades: ${stake}")

            # Calculate profits based on first successful trade (you may want to modify this logic)
            first_buy = buy_responses[0]
            self.profit_plus = first_buy["buy"].get("payout") - stake
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
        if outcome == "win":
            print(f"✓ Trade WON")
            profit = self.profit_plus

        elif outcome == "loss":
            print(f"✗ Trade LOST")
            profit = -self.profit_minus


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
            
            profit_change = profit
            
            self.balance_after = self.balance_previous + profit_change
            new_balance = self.balance_after
    
            self.current_balance = new_balance
            
            self.total_profit = self.current_balance - self.initial_balance

            self.balance_previous = self.balance_after            

            print(f"Balance: ${self.current_balance:.2f} | " +
                f"Change: ${(profit_change):+.2f} | " +
                f"P/L: ${self.total_profit:+.2f} | ")
            
            
            
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
                self.current_stake = 1.0  # Reset stake
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
        print(f"  - Initial stake 0.4")
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

    
    while True:
        try:
            barrier_under = int(input(f"Contract number under (default: 4): ") or (4))
            break
        except ValueError:
            print("Invalid input. Please enter a valid number.")



    # Previous digit count to be considered
    while True:
        try:
            prev_digits_count = int(input(f"Previous digit count (default: 6): ") or (6))
            break
        except ValueError:
            print("Invalid input. Please enter a valid integer number.")


    # Alter Pattern length count to be considered
    while True:
        try:
            alter_pattern_length = int(input(f"Alter Pattern length count (default: 4): ") or (4))
            break
        except ValueError:
            print("Invalid input. Please enter a valid integer number.")

    
    # Validate Noraml stake input (positive float)
    while True:
        try:
            normal_stake = float(input("Enter Normal stake amount (default: 1.0): ") or 1.0)
            if normal_stake <= 0:
                raise ValueError("Stake must be a positive number for Normal stake.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake for Normal stake.")


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
            stop_loss = float(input("Enter stop loss amount (default: 100): ") or 100)
            if stop_loss <= 0:
                raise ValueError("Stop loss must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stop loss.")

    return {
        "symbol": symbol,
        "api_token": api_token,
        "app_id": "66229",       # Application ID for Deriv API
        "normal_stake": normal_stake,
        #"is_over": is_over,
        "barrier_over": barrier_over,
        "barrier_under": barrier_under,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "wait_time": wait_time,
        "start_time": start_time,
        "wait_time_loss": wait_time_loss,
        "prev_digits_count": prev_digits_count,
        "alter_pattern_length": alter_pattern_length
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

