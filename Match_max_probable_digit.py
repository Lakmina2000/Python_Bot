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
        self.base_stake = 0  # Keep track of base stake
        self.wait_time_loss = config["wait_time_loss"]
        self.wait_time_win = config["wait_time"]
        self.symbol = self.config["symbol"]
        self.trade_type = None
        self.num_ticks = 1000
        
        # Balance tracking
        self.initial_balance = 0
        self.current_balance = 0
        
        self.currency = "USD"
        self.total_profit = 0
        self.trade_active = False
        self.last_trade_time = None
        
        # WebSocket connections
        self.ws_data = None  # For data/ticks
        self.ws_trading = None  # For trading
        
        # Running flag
        self.running = True
        
        # Trade history
        self.trades_history = []
        
        # Last tick data
        self.last_digit = None
        self.result = ""
        self.amount_revcovered = 0
        self.profit_sum = 0
        self.amount_revcovered_sum = 0
        self.result_final = ""
        time_now = dt.now().strftime("%H:%M:%S")
        self.trade_time_indicator = time_now
        self.profit_plus = 0
        self.profit_minus = 0
        self.target_type = ""
        self.trade_open = False
        self.wait_time = 0

        self.digits_list_count = []
        self.max_prob_list = []
        self.max_prob_list_main = []
        self.trade_treshold = 40
        self.trade_match_digit = -100
        self.martingal_trade_open = False
        self.martingale_trade_number = 0
        self.martingale_sequence = [0.35, 0.35, 0.35, 0.35, 0.35, 
                                    0.35, 0.35, 0.40, 0.45, 0.51,
                                    0.58, 0.66, 0.75, 0.86, 0.97,
                                    1.11, 1.26, 1.44, 1.64, 1.87,
                                    2.13, 2.42, 2.76, 3.14, 3.58,
                                    4.08, 4.65, 5.29, 6.03, 6.87,
                                    7.82, 8.91, 10.15, 11.56, 13.17,
                                    15.00, 17.08, 19.46] #, 22.16, 25.24, 28.74, 32.74, 37.28]
        self.digits = []
        self.current_stake = self.martingale_sequence[0]
        self.max_trade = 0
        self.max_gap = 0
        self.pairs = [[False, 0], [False, 0], [False, 0], [False, 0], [False, 0], [False, 0], [False, 0], [False, 0], [False, 0], [False, 0]]


    async def connect_websockets(self):
        """Establish all necessary WebSocket connections"""
        try:
            # Connect to main data websocket
            self.ws_data = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
            print("✓ Main WebSocket connected")
            
            # Connect to trading websocket
            self.ws_trading = await websockets.connect(f"wss://ws.derivws.com/websockets/v3?app_id={self.config['app_id']}")
         
            # Authenticate
            auth_response = await self.send_and_receive(self.ws_trading, {
                "authorize": self.config["api_token"]
            })
            
            if not auth_response or not auth_response.get("authorize"):
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

                        self.digits.append(self.last_digit)

                        if len(self.digits) >= 2:
                            if self.pairs[self.digits[-1]][0] == True:
                                self.pairs[self.digits[-1]][1] += 1

                            if self.digits[-2] == self.digits[-1]:
                                self.pairs[self.digits[-1]][0] = True
                                self.pairs[self.digits[-1]][1] = 0

                        print(self.pairs)

                        if self.trade_open:
                            if self.trade_match_digit == self.last_digit:
                                self.result = "win"
                                self.trade_open = False
                                self.pairs[self.trade_match_digit][0] = False

                            else:
                                self.result = "loss"
                                self.trade_open = False
                                self.martingal_trade_open = True
                        
                            await self.check_contract_result()
                            

                        # In normal mode, we wait for our target digit
                        if self.result == "loss":
                            waiting_time = self.wait_time_loss

                        elif self.result == "win":
                            waiting_time = self.wait_time_win

                        else:
                            waiting_time = self.wait_time

                        time_now = dt.now().strftime("%H:%M:%S")
                        time_now_obj = dt.strptime(time_now, "%H:%M:%S")
                        trade_time_obj = dt.strptime(self.trade_time_indicator, "%H:%M:%S")
                        time_difference = (time_now_obj - trade_time_obj).total_seconds()

                        if time_difference < waiting_time:
                            print(f"[{time_now}] ........ Waiting for Trade ({waiting_time - time_difference:.1f} seconds)")
                        
                        print(f"[{current_time}] Last Digit: {self.last_digit} | Total Profit: {round(self.total_profit, 2)} | Max Trade Number: {self.max_trade} |")
                        print("--------------------------------------------------------------------------------------------")


                        if (time_difference >= waiting_time) and (not self.trade_open):

                            if not self.trade_open:
                                max_individuals = 0
                                selected_digit = 100
                                for i in range(len(self.pairs)):
                                    if (self.pairs[i][0] == True) and (self.pairs[i][1] > max_individuals):
                                        max_individuals = self.pairs[i][1]
                                        selected_digit = i
                                
                                if (selected_digit != 100) and (selected_digit == self.digits[-1]) and (max_individuals > 25):
                                    print(f"✓ Target number found! .... Placing Match trade for {selected_digit}...")
                                    self.trade_active = True
                                    self.trade_open = True
                                    self.trade_match_digit = selected_digit
                                    # Place trade
                                    await self.place_trade()
                        
                            
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
        

    async def place_trade(self):
        """Place a MATCH trade with the specified parameters - Optimized for speed"""
        try:
            # Round stake to 2 decimal places
            stake = round(self.current_stake, 2)
            
            contract_type = "DIGITMATCH"  # Correct contract type for MATCH trades
            
            # Validate trade_match_digit is set and valid (0-9)
            if not hasattr(self, 'trade_match_digit') or self.trade_match_digit is None:
                print("Error: trade_match_digit not set")
                self.trade_active = False
                return "error"
            
            if not (0 <= self.trade_match_digit <= 9):
                print(f"Error: Invalid trade_match_digit: {self.trade_match_digit}. Must be 0-9")
                self.trade_active = False
                return "error"
            
            # Create proposal message
            proposal_msg = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": self.currency,
                "duration": 1,
                "duration_unit": "t",
                "symbol": "R_10",
                "barrier": self.trade_match_digit  # Use 'barrier' instead of 'digit'
            }
            
            # Send proposal
            proposal_result = await self.send_and_receive(self.ws_trading, proposal_msg)
            
            if not proposal_result or "proposal" not in proposal_result:
                print(f"Failed to get proposal for {contract_type}")
                self.trade_active = False
                return "error"
            
            proposal = proposal_result["proposal"]
            if isinstance(proposal, list):
                proposal = proposal[0]  # Take first if it's a list
            
            contract_id = proposal.get("id")
            if not contract_id:
                print("No valid proposal received")
                self.trade_active = False
                return "error"
            
            # Validate proposal before buying
            if "error" in proposal:
                print(f"Proposal error: {proposal['error']['message']}")
                self.trade_active = False
                return "error"
            
            # Create buy order
            buy_result = await self.send_and_receive(self.ws_trading, {
                "buy": contract_id,
                "price": stake,
            })
            
            if not buy_result or "buy" not in buy_result:
                print(f"Buy request failed for {contract_type}")
                self.trade_active = False
                return "error"
            
            # Check for buy errors
            buy_data = buy_result["buy"]
            if "error" in buy_data:
                print(f"Buy error: {buy_data['error']['message']}")
                self.trade_active = False
                return "error"
            
            # Record trade time
            trade_time = dt.now().strftime("%H:%M:%S")
            self.trade_time_indicator = trade_time
            
            # Log successful trade with digit information
            print(f"[{trade_time}] Placed MATCH trade: ${stake} on digit {self.trade_match_digit}")
            
            # Calculate profits
            payout = buy_data.get("payout", 0)
            if payout > 0:
                self.profit_plus = payout - stake  # Net profit if win
                self.profit_minus = stake          # Loss if trade fails
            else:
                print("Warning: No payout information received")
                self.profit_plus = 0
                self.profit_minus = stake
            
            # Store contract details for tracking
            self.current_contract_id = buy_data.get("contract_id")
            
            return "success"
            
        except Exception as e:
            print(f"Error in place_trade: {e}")
            self.trade_active = False
            return "error"


    async def check_contract_result(self):
            outcome = self.result
            
            # Process result
            if outcome == "win":
                print(f"✓ Trade WON... Trade Number: {self.martingale_trade_number + 1}")
                if self.max_trade < self.martingale_trade_number + 1:
                    self.max_trade = (self.martingale_trade_number + 1)
                self.martingale_trade_number = 0
                self.current_stake = self.martingale_sequence[self.martingale_trade_number]
                profit = self.profit_plus

            elif outcome == "loss":
                print(f"✗ Trade LOST... Trade Number: {self.martingale_trade_number + 1}")
                if self.max_trade < self.martingale_trade_number + 1:
                    self.max_trade = (self.martingale_trade_number + 1)
                self.martingale_trade_number += 1
                
                self.current_stake = self.martingale_sequence[self.martingale_trade_number]
                profit = -self.profit_minus
                
            # Checking the result of the prev trade
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
            old_balance = self.balance_previous
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
                "balance": self.current_balance
            })

            self.result = ""

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
                self.trade_active = False
                self.running = True  # Resume the bot
                
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
        print(f"Trading {self.config['symbol']} with Odd/Even strategy:")
        print(f"  - Initial stake ${self.base_stake}")
        print(f"  - Trade type: ODD or EVEN")
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


    # Get API token
    api_token = input(f"Enter API Token for bot: ").strip()
    while not api_token:
        print("Error: API token cannot be empty.")
        api_token = input(f"Enter API Token for bot: ").strip()
    

    # Get symbol with default
    symbol = input("Enter symbol (default: R_10): ") or "R_10"


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
            if wait_time_loss < 0:
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
            stop_loss = float(input("Enter stop loss amount (default: 120): ") or 120)
            if stop_loss <= 0:
                raise ValueError("Stop loss must be a positive number.")
            break
        except ValueError as e:
            print(f"Invalid input: {e}. Please enter a valid stop loss.")

    return {
        "symbol": symbol,
        "api_token": api_token,
        "app_id": "66229",       # Application ID for Deriv API
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "wait_time": wait_time,
        "start_time": start_time,
        "wait_time_loss": wait_time_loss
    }


async def main():
    """Main entry point for the application"""
    print("====== DIGIT TRADING BOT ======")
    print("This bot places Odd/Even trades based on digit analysis")
    
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