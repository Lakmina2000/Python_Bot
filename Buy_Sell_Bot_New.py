import json
import asyncio
import websockets
from datetime import datetime as dt, timedelta
import datetime
import time


class CandleBot:
    def __init__(self, config):
        # Store configuration
        self.config = config
        
        # Bot trade state variables
        self.current_stake = config["stake"]
        self.base_stake = config["stake"]  # Keep track of base stake
        self.martingale_stake_list = [0.35, 0.8, 1.7, 3.54, 7.39, 15.3, 32.1, 66.34]
        self.martingale_trade_number = 0
        
        # Balance tracking
        self.initial_balance = 0
        self.current_balance = 0
        self.balance_previous = 0
        self.balance_after = 0
        
        self.currency = "USD"
        self.total_profit = 0
        self.trade_active = False
        self.last_trade_time = None
        
        # WebSocket connections
        self.ws_data = None  # For data/ticks
        self.ws_trading = None  # For trading
        self.ws_candles = None  # For candle data
        
        # Running flag
        self.running = True
        self.contract_id = None
        
        # Candle tracking
        self.candle_history = []
        self.candle_period = 60  # 1-minute candles
        self.candles_needed = 3  # We need 3 consecutive candles
        
        # Martingale strategy
        self.in_martingale_mode = False
        self.martingale_step = 0
        self.martingale_sequence_bear = ["bearish", "bearish", "bearish", "bearish", "bearish", "bearish", "bearish", "bearish"]
        self.martingale_sequence_bull = ["bullish", "bullish", "bullish", "bullish", "bullish", "bullish", "bullish", "bullish"]
        self.current_sequence = []
        self.original_loss = 0
        self.recovered_amount = 0
        
        # Results tracking
        self.trade_count = 0
        self.win_count = 0
        self.loss_count = 0
        
        # Tasks
        self.tick_monitor_task = None
        self.candle_monitor_task = None
        
        # Trade results
        self.last_trade_profit = 0
        self.last_trade_result = ""
        self.current_time = None

        # This is the related variables for the quote
        self.quote = None
        self.candel_start_price = None
        self.candel_stop_price = None
        self.candel_type = None
        self.bid = None
        self.ask = None

        # Those variables for place the trade 
        self.trade_placed = False
        self.trade_candle_fiished = False
        self.profit_plus = None
        self.profit_minus = None
        self.last_type_applied = None

        # Other variables 
        self.started = False
        self.number_of_candles_check = config["candles_count"]
        self.number_of_candles_check_recovary = config["candles_count_recovary"]
        self.rotate_constrants = False
        self.mid_win = False
        self.start_recovary_trade = False
        self.max_total_profit = 0
        self.max_total_loss = 0
        
        # Custom offset
        self.offset = timedelta(hours=-5, minutes=-30, seconds=-00)
        self.custom_time = dt.now() + self.offset


    async def connect_websockets(self):
        """Establish all necessary WebSocket connections"""
        try:
            # Connect to main data websocket
            self.ws_data = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            print("✓ Data WebSocket connected")
            
            # Connect to trading websocket
            self.ws_trading = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            # Authenticate
            auth_response = await self.send_and_receive(self.ws_trading, {
                "authorize": self.config["api_token"]
            })
            
            if not auth_response or not auth_response.get("authorize"):
                print("✗ Authentication failed")
                return False
            
            print(f"✓ Rise / Fall Trading WebSocket authenticated")
            
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
            start_datetime = self.custom_time.strptime(self.config["start_time"], "%d-%m-%Y %H:%M:%S")
            
            # Get current time
            current_time = self.custom_time
            
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
                        self.quote = float(data["tick"]["quote"])
                        self.ask = float(data["tick"]["ask"])
                        self.bid = float(data["tick"]["bid"])
                        
                        # Print tick with timestamp
                        
                        # Apply the offset to current UTC time
                        self.custom_time = dt.now() + self.offset
                        self.current_time = self.custom_time.strftime("%H:%M:%S")
                        time_str = str(self.current_time)
                        print(f"[{self.current_time}] Quote: {self.quote} | Current Profit: ${self.total_profit:+.2f} | Max Profit: ${self.max_total_profit:+.2f} | Max Loss: ${self.max_total_loss:+.2f}")
                    
                        await self.create_last_candle()
                        if not self.in_martingale_mode and (int(time_str[-2:]) >= 58 and int(time_str[-2:]) <= 59.5):
                            trade_type = await self.check_last_candle_pattern()
                            if trade_type == "bearish":
                                self.trade_type_currect = trade_type
                                trade_type = None
                                await self.place_trade("bearish")
                            elif trade_type == "bullish":
                                self.trade_type_currect = trade_type
                                trade_type = None
                                await self.place_trade("bullish")

                        elif self.in_martingale_mode and (int(time_str[-2:]) >= 58 and int(time_str[-2:]) <= 59.5): ################# < 59

                            if self.mid_win:
                                result = await self.check_last_candle_pattern_recovary()

                                if result == "bearish":
                                    self.current_sequence = self.martingale_sequence_bear
                                
                                    # Calculate first martingale stake
                                    self.current_stake = self.martingale_stake_list[0]
                                    self.martingale_trade_number = 1
                                    self.mid_win = False
                                
                                elif result == "bullish":
                                    self.current_sequence = self.martingale_sequence_bull
                                
                                    # Calculate first martingale stake
                                    self.current_stake = self.martingale_stake_list[0]
                                    self.martingale_trade_number = 1
                                    self.mid_win = False


                            if self.start_recovary_trade or (not self.mid_win):
                                trade_type_2 = str(self.current_sequence[self.martingale_trade_number - 1])
                                self.trade_type_currect = trade_type_2
                                self.start_recovary_trade = False
                                await self.place_trade(trade_type_2)
                            
                            
            
                        """if self.in_recovery_mode and (not self.mid_win):
                            target = self.recovery_sequence[self.recovery_index] if self.recovery_index < len(self.recovery_sequence) else '?'
                            target_text = 'Even' if target == 0 else 'Odd'
                            print(f'[{current_time}] Last Digit: {self.last_digit}  |  Recovery Target: {target_text}')
                        elif self.mid_win:
                            print(f'[{current_time}] Last Digit: {self.last_digit}  |  Target: {self.current_target_digit}')
                        else:
                            print(f'[{current_time}] Last Digit: {self.last_digit}  |  Target: {self.current_target_digit}')"""
                                                   
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

    async def place_trade(self, trade_type):
        """Place a binary option trade"""
        try:
            if self.trade_active:
                print("Cannot place new trade: previous trade still active")
                return False
                
            # make the tade_placed varible in to True to ensure the trade is placed
            self.trade_placed = True

            contract_type = "CALL" if trade_type == "bullish" else "PUT"
            
            # Store previous balance before trade
            self.balance_previous = self.current_balance
            
            # Place the trade
            trade_response = await self.send_and_receive(self.ws_trading, {
                "buy": 1,
                "price": self.current_stake,
                "parameters": {
                    "amount": self.current_stake,
                    "basis": "stake",
                    "contract_type": contract_type,
                    "currency": self.currency,
                    "duration": 1,
                    "duration_unit": "m",
                    "symbol": self.config["symbol"]
                }
            })
            
            if trade_response and "error" in trade_response:
                print(f"Trade error: {trade_response['error']['message']}")
                return False
                
            if not trade_response or not trade_response.get("buy"):
                print("Failed to place trade: unknown error")
                return False
                
            # Extract contract info
            contract_info = trade_response.get("buy", {})
            self.contract_id = contract_info.get("contract_id")

            # take the profit amounts for the applied trade
            self.profit_plus = trade_response["buy"].get("payout") - self.current_stake
            #print(self.profit_plus)
            self.profit_minus = self.current_stake
            #print(self.profit_minus)
            
            # Update trade state
            self.trade_active = True
            # Apply the offset to current UTC time
            self.custom_time = dt.now() + self.offset
            self.last_trade_time = self.custom_time
            
            # Calculate timestamp for precise expiry monitoring
            start_time = int(contract_info.get("start_time", time.time()))
            expiry_time = start_time + 58  # 1 minute expiry
            
            # Print trade information
            direction = "RISE" if trade_type == "bullish" else "FALL"
            mode = "MARTINGALE RECOVERY" if self.in_martingale_mode else "NORMAL"
            print(f"\n[{self.custom_time.strftime('%H:%M:%S')}] {mode} Trade #{self.trade_count}: {direction} - ${self.current_stake}")
            
            if self.in_martingale_mode:
                print(f"Recovery Step: {self.martingale_trade_number + 1}/{len(self.current_sequence)}")
                print(f"Amount to Recover: ${abs(self.base_stake):.2f} | Recovered so far: ${self.recovered_amount:.2f}")
            
            return True
            
        except Exception as e:
            print(f"Error placing trade: {e}")
            return False
            
    async def check_trade_expiry(self, time_str):
        """Fallback to close trade if no result received by expiry"""
        if self.trade_placed:
            if self.trade_placed and (int(time_str[-2:]) >= 58):
                print(f"[{time_str}] Trade expiry timeout reached. Checking final status...")
                self.trade_candle_fiished = True
                self.trade_placed = False
                self.trade_active = False
                self.rotate_constrants = True
                await self.process_trade_result()

            
    async def process_trade_result(self):
        """Process the result of a completed trade"""
        
        try:
            # Extract result information
            result_direction = 0
            if self.trade_type_currect == "bullish":
                result_direction = (float(self.candle_history[-1]['close']) - float(self.candle_history[-1]['open']))
            elif self.trade_type_currect == "bearish":
                result_direction = (float(self.candle_history[-1]['open']) - float(self.candle_history[-1]['close']))

            if result_direction > 0:
                profit = self.profit_plus
            else:
                profit = - self.profit_minus
            
            # Update balance
            self.current_balance += profit
            self.balance_after = self.current_balance
            self.total_profit += profit
            self.last_trade_profit = profit
            
            # Determine if trade won or lost
            is_win = True if profit >= 0 else False
            result = "WON" if is_win else "LOST"
            self.last_trade_result = result
            
            # Update counters
            if is_win:
                self.win_count += 1
            else:
                self.loss_count += 1
            
            # Print result
            print(f"Trade {result}: ${profit:.2f}")
            print(f"Balance: ${self.current_balance:.2f} | Change: ${profit:.2f} | P/L: ${self.total_profit:.2f}", end="")

            if self.max_total_profit < self.total_profit:
                self.max_total_profit = self.total_profit
            
            if self.max_total_loss > self.total_profit:
                self.max_total_loss = self.total_profit 
            
            # Handle martingale mode specifics
            if self.in_martingale_mode:
                self.recovered_amount += profit
                print(f" | Recovered Amount: ${self.recovered_amount:.2f} / ${abs(self.base_stake):.2f}")
                
                # Check if we've recovered the loss
                if self.recovered_amount >= abs(self.base_stake):
                    print("\n✓ Loss recovered successfully!")
                    # Exit martingale mode
                    self.in_martingale_mode = False
                    self.current_stake = self.base_stake  # Reset stake
                    self.recovered_amount = 0
                    self.original_loss = 0
                    self.martingale_trade_number = 0
                    self.mid_win = False
                    self.start_recovary_trade = False
                
                else:
                    # Increase stake after loss
                    self.current_stake = self.martingale_stake_list[self.martingale_trade_number]
                    self.martingale_trade_number += 1
                    
                    # Reset the stake in to the satrting stake
                    if is_win:
                        self.mid_win = True

            else:
                print("")  # Just add newline
                # If normal mode trade lost, enter martingale mode
                if not is_win:
                    print("\n! Entering martingale recovery mode")
                    self.in_martingale_mode = True
                    self.original_loss = profit  # Store the loss to recover
                    self.recovered_amount = 0
                    
                    # Set martingale sequence based on last candle pattern

                    is_bullish_pattern = True if await self.check_last_candle_pattern_recovary() == "bullish" else False

                    self.current_sequence = self.martingale_sequence_bull if is_bullish_pattern else self.martingale_sequence_bear
                    
                    # Calculate first martingale stake
                    self.current_stake = self.martingale_stake_list[self.martingale_trade_number]
                    self.martingale_trade_number += 1
                else:
                    # Reset stake after win in normal mode
                    self.current_stake = self.base_stake
            
            # Check take profit and stop loss
            if self.total_profit >= self.config["take_profit"]:
                print(f"\n✓ Take profit target reached: ${self.total_profit:.2f}")
                await self.ask_to_continue()
            elif abs(self.total_profit) >= self.config["stop_loss"] and self.total_profit < 0:
                print(f"\n✗ Stop loss triggered: ${self.total_profit:.2f}")
                await self.ask_to_continue()
                
            # Reset trade active flag
            self.trade_active = False
            self.contract_id = None
            
        except Exception as e:
            print(f"Error processing trade result: {e}")
            # Ensure trade is marked as complete even on error
            self.trade_active = False
            self.contract_id = None

    async def check_last_candle_pattern(self):
        """Check if last 3 candles are bullish or bearish"""
        if len(self.candle_history) < self.number_of_candles_check:
            return None
            
        if not self.trade_active:
            # Get last 3 candles
            last_candels = self.candle_history[-self.number_of_candles_check:]
            
            # Check if all are bullish (close > open)
            all_bullish = all(candle['close'] > candle['open'] for candle in last_candels)
            
            # Check if all are bearish (close < open)
            all_bearish = all(candle['close'] < candle['open'] for candle in last_candels)
            
            if all_bullish:
                self.last_type_applied = "bearish"
                return "bearish"
            elif all_bearish:
                self.last_type_applied = "bullish"
                return "bullish"
            else:
                self.last_type_applied = None
                return None
        else:
            return None

    async def check_last_candle_pattern_recovary(self):
        """Check if last 3 candles are bullish or bearish"""
        if len(self.candle_history) < self.number_of_candles_check_recovary:
            return None
            
        if not self.trade_active:
            # Get last 3 candles
            last_candels = self.candle_history[-self.number_of_candles_check_recovary:]
            
            # Check if all are bullish (close > open)
            all_bullish = all(candle['close'] > candle['open'] for candle in last_candels)
            
            # Check if all are bearish (close < open)
            all_bearish = all(candle['close'] < candle['open'] for candle in last_candels)
            
            if all_bullish:
                self.start_recovary_trade = True
                return "bearish"
            elif all_bearish:
                self.start_recovary_trade = True
                return "bullish"
            else:
                self.start_recovary_trade = False
                return None
        else:
            return None

    async def create_last_candle(self):
        # Taking the current time for the refferance 
        time_str = str(self.current_time)
    
        if int(time_str[-2:]) == 0: # or int(time_str[-2:]) == 1:
            self.started = True
        
        if self.started:
            if int(time_str[-2:]) >= 0 and int(time_str[-2:]) <= 58:
                print(f"[{self.current_time}] Candle is Started {60 - int(time_str[-2:])} seconds remaining .....")
                
            if int(time_str[-2:]) >= 0 and int(time_str[-2:]) < 2:
                self.candel_start_price = self.quote
                self.rotate_constrants = False

            elif (int(time_str[-2:]) >= 58) and (not self.rotate_constrants):
                self.candel_stop_price = self.quote
                print(f"[{self.current_time}] Candle Data Observation is Stopped .....")
                print(f"[{self.current_time}] A Candle has been added to the History list .....")

                if (self.candel_stop_price - self.candel_start_price) >= 0:
                    self.candel_type = "bullish"
                    print(f"[{self.current_time}] Previous Candle is a BULLISH !!!.....")

                else:
                    self.candel_type = "bearish"
                    print(f"[{self.current_time}] Previous Candle is a BEARISH !!!.....")

                self.candle_history.append({
                    'open': self.candel_start_price,
                    'close': self.candel_stop_price,
                    'type': self.candel_type
                })
            
                await self.check_trade_expiry(time_str)
        

    async def ask_to_continue(self):
        """Ask user if they want to continue trading after TP/SL hit"""
        self.running = False  # Stop the bot temporarily
        
        print("\n" + "="*50)
        print("BOT STOPPED - PROFIT TARGET OR STOP LOSS REACHED")
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
                self.tick_monitor_1 = None
                # Create and run the bot
                bot = CandleBot(config)
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
            
        trade_type = "RAISE / FALL"
        
        print("\n" + "="*50)
        print("===== CANDLE SEARCHING BOT STARTED =====")
        print(f"Trading {self.config['symbol']} with {trade_type} strategy:")
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
    print("CANDLE PATTERN TRADING BOT CONFIGURATION")
    print("="*50)
    
    # Get API token
    api_token = input("Enter API Token: ").strip()
    while not api_token:
        print("Error: API token cannot be empty.")
        api_token = input("Enter API Token: ").strip()
    
    # Get symbol with default
    symbol = input("Enter symbol (default: R_10): ") or "R_10"
    
    # Validate normal stake input (positive float)
    while True:
        try:
            normal_stake = float(input("Enter normal stake amount (default: 1.0): ") or 1.0)
            if normal_stake <= 0:
                raise ValueError("Stake must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake amount.")

    # Get start time
    # Custom offset
    offset = timedelta(hours=-5, minutes=-29, seconds=-59)

    # Apply the offset to current UTC time
    current_time = dt.now() + offset
    default_start_time = (current_time + datetime.timedelta(seconds=20)).strftime("%d-%m-%Y %H:%M:%S")
    
    while True:
        try:
            start_time = input(f"Enter start time (format: DD-MM-YYYY HH:MM:SS, default: {default_start_time}): ") or default_start_time
            # Validate format
            dt.strptime(start_time, "%d-%m-%Y %H:%M:%S")
            break
        except ValueError:
            print("Invalid date format. Please use DD-MM-YYYY HH:MM:SS")

    # Validate candles_count (positive int)
    while True:
        try:
            candles_count = int(input("Enter Candles count for Normal strategy (default: 3): ") or 3)
            if candles_count <= 0:
                raise ValueError("Candles count for normal strategy must be a positive integer number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid candles count.")


    # Validate candles_count_recovery (positive int)
    while True:
        try:
            candles_count_recovary = int(input("Enter Candles count for Recovery strategy (default: 4): ") or 4)
            if candles_count_recovary <= 0:
                raise ValueError("Candles count for Recovery strategy must be a positive integer number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid recovery candles count.")

        
    # Validate take_profit (positive float)
    while True:
        try:
            take_profit = float(input("Enter take profit amount (default: 20): ") or 20)
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
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "start_time": start_time,
        "candles_count": candles_count,
        "candles_count_recovary": candles_count_recovary
    }

async def main():
    """Main entry point for the application"""
    print("====== CANDLE PATTERN TRADING BOT ======")
    print("This bot trades based on 3 consecutive candle patterns with Martingale recovery")
    
    try:
        # Get configuration from user
        config = await get_user_config()
        
        # Create and run the bot
        bot = CandleBot(config)
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
    
    #"api_token": zdCm5RYG6ukNK1S, # Lakmina
    #"api_token": dVYWygMRmj6u6IU, # Janith C5CV8Zk3FFrlpWn
