import json
import asyncio
import websockets
from datetime import datetime as dt, timedelta
import datetime
import time
import pandas as pd
from collections import defaultdict


class CandleBot:
    def __init__(self, config):
        # Store configuration
        self.config = config
        
        # Bot trade state variables
        self.current_stake = config["stake"]
        self.base_stake = config["stake"]  # Keep track of base stake
        self.martingale_stake_list = [0.8, 1.7, 3.54, 7.39, 15.3, 32.1, 66.34]
                                  # [1, 1.42, 2.92, 5.99, 12.30, 25.25, 51.82]
                                  # [1, 2.05, 4.21, 8.64, 17.73, 36.4, 74.71]
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
        self.number_of_candles_check = 100
        self.rotate_constrants = False
        self.max_total_profit = 0
        self.max_total_loss = 0
        
        # Custom offset
        self.offset = timedelta(hours=-5, minutes=-30, seconds=0)
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
            # Step 3: Wait until current minute is near the end to start live loop
            while True:
                now = dt.now() + self.offset
                if now.second >= 58:
                    break

            # Step 1: Request 40 historical ticks (1-minute candles will be created from this)
            print("Requesting historical tick data...")
            await self.ws_trading.send(json.dumps({
                "ticks_history": self.config["symbol"],
                "count": 120 * 30,  # Assuming 1 tick per 2 second approx, for 40 minutes
                "end": "latest",
                "start": 1,
                "style": "ticks"
            }))

            candles_data = await self.ws_trading.recv()
            candles_data = json.loads(candles_data)

            if "error" in candles_data:
                print(f"Error fetching ticks: {candles_data['error']['message']}")
                return False

            # Step 2: Create 1-minute candles from tick history
            tick_list = candles_data["history"]["prices"]
            time_list = candles_data["history"]["times"]
            temp_ticks = [{"epoch": time_list[i], "price": float(tick_list[i])} for i in range(len(tick_list))]

            await self.aggregate_ticks_to_candles(temp_ticks)
            print(f"Prepared {len(self.candle_history)} historical candles.")

            # Step 4: Start real-time tick monitoring
            while self.running:
                
                try:
                    response = await asyncio.wait_for(self.ws_data.recv(), timeout=1.0)
                    data = json.loads(response)

                    if data.get("msg_type") == "tick":
                        self.quote = float(data["tick"]["quote"])
                        self.ask = float(data["tick"]["ask"])
                        self.bid = float(data["tick"]["bid"])

                        self.custom_time = dt.now() + self.offset
                        self.current_time = self.custom_time.strftime("%H:%M:%S")
                        time_str = self.current_time

                        print(f"[{self.current_time}] Quote: {self.quote} | Current Profit: ${self.total_profit:+.2f} | Max Profit: ${self.max_total_profit:+.2f} | Max Loss: ${self.max_total_loss:+.2f}")

                        await self.create_last_candle()

                        # Trade decision logic at end of each minute
                        if not self.in_martingale_mode and 58 <= self.custom_time.second <= 59:

                            trade_type = await self.check_last_candle_pattern()

                            if trade_type == "bearish":
                                self.trade_type_currect = trade_type
                                await self.place_trade("bearish")

                            elif trade_type == "bullish":
                                self.trade_type_currect = trade_type
                                await self.place_trade("bullish")

                        elif self.in_martingale_mode and 58 <= self.custom_time.second <= 59:
                            
                            trade_type_2 = str(self.current_sequence[self.martingale_trade_number - 1])
                            self.trade_type_currect = trade_type_2

                            trade_type = await self.check_last_candle_pattern()

                            if trade_type == "bearish":
                                self.trade_type_currect = trade_type
                                await self.place_trade("bearish")
                            
                            elif trade_type == "bullish":
                                self.trade_type_currect = trade_type
                                await self.place_trade("bullish")
                            else:
                                await self.place_trade(trade_type_2)


                except asyncio.TimeoutError:
                    continue

                except Exception as e:
                    print(f"Error processing tick: {e}")
                    await asyncio.sleep(0.5)
                    continue

        except asyncio.CancelledError:
            print("Tick monitoring cancelled")

        except Exception as e:
            print(f"Error monitoring ticks: {e}")


    async def aggregate_ticks_to_candles(self, ticks):

        grouped = defaultdict(list)
        for tick in ticks:
            minute_ts = tick["epoch"] - (tick["epoch"] % 60)  # Group ticks by minute
            grouped[minute_ts].append(tick["price"])

        candles = []
        for ts in sorted(grouped.keys()):
            prices = grouped[ts]
            candle = {
                "timestamp": ts,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1]
            }
            candles.append(candle)

        # Keep only last 40 candles
        self.candle_history.extend(candles[-100:])

        # Print candles as UP or DOWN
        print("\nLast 40 Candles:")
        for i, candle in enumerate(self.candle_history[-100:], 1):
            direction = "UP" if candle["close"] > candle["open"] else "DOWN" if candle["close"] < candle["open"] else "FLAT"
            print(f"Candle {i:>2}: {direction} | Open: {candle['open']} | Close: {candle['close']}")

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
            if result == "WON":
                print(f"✅Trade {result}: ${profit:.2f}")
            else:
                print(f"❌Trade {result}: ${profit:.2f}")

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
                    print("\n✅ Loss recovered successfully!")
                    # Exit martingale mode
                    self.in_martingale_mode = False
                    self.current_stake = self.base_stake  # Reset stake
                    self.recovered_amount = 0
                    self.original_loss = 0
                    self.martingale_trade_number = 0
                
                else:
                    # Increase stake after loss
                    self.current_stake = self.martingale_stake_list[self.martingale_trade_number]
                    self.martingale_trade_number += 1

            else:
                print("")  # Just add newline
                # If normal mode trade lost, enter martingale mode
                if not is_win:
                    print("\n❗Entering martingale recovery mode")
                    self.in_martingale_mode = True
                    self.original_loss = profit  # Store the loss to recover
                    self.recovered_amount = 0
                    
                    # Set martingale sequence based on last candle pattern

                    is_bullish_pattern = True if self.trade_type_currect == "bullish" else False

                    self.current_sequence = self.martingale_sequence_bull if is_bullish_pattern else self.martingale_sequence_bear
                    
                    # Calculate first martingale stake
                    self.current_stake = self.martingale_stake_list[self.martingale_trade_number]
                    self.martingale_trade_number += 1

                else:
                    # Reset stake after win in normal mode
                    self.current_stake = self.base_stake
            
            # Check take profit and stop loss
            if self.total_profit >= self.config["take_profit"]:
                print(f"\n✅ Take profit target reached: ${self.total_profit:.2f}")
                await self.ask_to_continue()
            elif abs(self.total_profit) >= self.config["stop_loss"] and self.total_profit < 0:
                print(f"\n❌ Stop loss triggered: ${self.total_profit:.2f}")
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
        """Check for MACD crossover in the last candle"""
        # Need more candles for accurate MACD calculation
        # 26 (for EMA26) + 9 (for signal) + buffer = at least 50-60 candles recommended
        min_candles_needed = max(60, self.number_of_candles_check)
        
        if len(self.candle_history) < min_candles_needed:
            return None
            
        if not self.trade_active:
            # Get more historical data for accurate EMA calculation
            last_candles = self.candle_history[-min_candles_needed:]
            
            # Convert to DataFrame - ensure proper column names
            df = pd.DataFrame(last_candles)
            
            # Ensure 'close' column exists and is numeric
            if 'close' not in df.columns:
                # Adjust based on your actual column structure
                # Common alternatives: 'Close', 'c', or index position
                print("Warning: 'close' column not found. Check your candle data structure.")
                return None
                
            # Convert to numeric in case of string values
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            
            # Remove any NaN values
            df = df.dropna(subset=['close'])
            
            if len(df) < min_candles_needed:
                print("Insufficient valid price data after cleaning")
                return None
            
            # Calculate EMAs with proper parameters
            # Using adjust=False is correct for trading calculations
            df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
            df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
            
            # Calculate MACD line (fast EMA - slow EMA)
            df['macd'] = df['ema12'] - df['ema26']
            
            # Calculate Signal line (9-period EMA of MACD)
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            
            # Calculate MACD Histogram (optional but useful for debugging)
            df['histogram'] = df['macd'] - df['signal']
            
            # Get the last two values for crossover detection
            if len(df) < 3:
                return None
                
            macd_prev_2 = df['macd'].iloc[-3]
            signal_prev_2 = df['signal'].iloc[-3]    
            macd_prev = df['macd'].iloc[-2]
            signal_prev = df['signal'].iloc[-2]
            macd_now = df['macd'].iloc[-1]
            signal_now = df['signal'].iloc[-1]
            
            # Enhanced debugging output
            print(f"Before Previous - MACD: {macd_prev_2:.6f} | Signal: {signal_prev:.6f} | Histogram: {macd_prev_2 - signal_prev_2:.6f}")
            print(f"Previous - MACD: {macd_prev:.6f} | Signal: {signal_prev:.6f} | Histogram: {macd_prev - signal_prev:.6f}")
            print(f"Current  - MACD: {macd_now:.6f} | Signal: {signal_now:.6f} | Histogram: {macd_now - signal_now:.6f}")
            print(f"Close prices - Before Previous: {df['close'].iloc[-3]:.2f} | Previous: {df['close'].iloc[-2]:.2f} | Current: {df['close'].iloc[-1]:.2f}")
            
            # Check for crossover with small tolerance to avoid false signals from rounding
            tolerance = 0
            
            if self.martingale_trade_number < 4:
                # Bullish crossover: MACD crosses above Signal
                if (macd_prev <= (signal_prev - tolerance)) and (macd_now >= (signal_now + tolerance)):
                    self.last_type_applied = "bullish"
                    print("✅ Bullish MACD crossover detected")
                    return "bullish"
                # Bearish crossover: MACD crosses below Signal  
                elif (macd_prev >= (signal_prev + tolerance)) and (macd_now <= (signal_now - tolerance)):
                    self.last_type_applied = "bearish"
                    print("✅ Bearish MACD crossover detected")
                    return "bearish"
                else:
                    self.last_type_applied = None
                    return None
                
            else:
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
            normal_stake = float(input("Enter normal stake amount (default: 0.35): ") or 0.35)
            if normal_stake <= 0:
                raise ValueError("Stake must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stake amount.")


    # Get start time
    # Custom offset
    offset = timedelta(hours=-5, minutes=-30, seconds=0)

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
        "stake": normal_stake,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "start_time": start_time
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
