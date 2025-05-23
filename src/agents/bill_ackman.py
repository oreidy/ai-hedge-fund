from langchain_openai import ChatOpenAI
from graph.state import AgentState, show_agent_reasoning
from tools.api import get_financial_metrics, get_market_cap, search_line_items
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
import json
from typing_extensions import Literal
from utils.progress import progress
from utils.llm import call_llm
from utils.logger import logger

class BillAckmanSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float
    reasoning: str


def bill_ackman_agent(state: AgentState):
    """
    Analyzes stocks using Bill Ackman's investing principles and LLM reasoning.
    Fetches multiple periods of data so we can analyze long-term trends.
    """

    # Get verbose_data from metadata or default to False
    verbose_data = state["metadata"].get("verbose_data", False)
    logger.debug("Accessing Bill Ackman Agent", module="bill_ackman_agent")
    
    data = state["data"]
    end_date = data["end_date"]
    tickers = data["tickers"]

    analysis_data = {}
    ackman_analysis = {}
    
    for ticker in tickers:
        progress.update_status("bill_ackman_agent", ticker, "Fetching financial metrics")
        # You can adjust these parameters (period="annual"/"ttm", limit=5/10, etc.)
        metrics = get_financial_metrics(ticker, end_date, period="annual", limit=5, verbose_data = verbose_data)
 
        # Debug:
        if verbose_data:
            if metrics:
                logger.debug(f"Retrieved {len(metrics)} periods of financial metrics", 
                           module="bill_ackman_agent", ticker=ticker)
            else:
                logger.error(f"No financial metrics retrieved for {ticker}", 
                           module="bill_ackman_agent", ticker=ticker)
        
        progress.update_status("bill_ackman_agent", ticker, "Gathering financial line items")
        # Request multiple periods of data (annual or TTM) for a more robust long-term view.
        financial_line_items = search_line_items(
            ticker,
            [
                "revenue",
                "operating_margin",
                "debt_to_equity",
                "free_cash_flow",
                "total_assets",
                "total_liabilities",
                "dividends_and_other_cash_distributions",
                "outstanding_shares"
            ],
            end_date,
            period="annual",  # or "ttm" if you prefer trailing 12 months
            limit=5,           # fetch up to 5 annual periods (or more if needed)
            verbose_data = verbose_data
        )
        
        progress.update_status("bill_ackman_agent", ticker, "Getting market cap")
        market_cap = get_market_cap(ticker, end_date, verbose_data)
        
        progress.update_status("bill_ackman_agent", ticker, "Analyzing business quality")
        quality_analysis = analyze_business_quality(metrics, financial_line_items, verbose_data)
        
        progress.update_status("bill_ackman_agent", ticker, "Analyzing balance sheet and capital structure")
        balance_sheet_analysis = analyze_financial_discipline(metrics, financial_line_items, verbose_data)
        
        progress.update_status("bill_ackman_agent", ticker, "Calculating intrinsic value & margin of safety")
        valuation_analysis = analyze_valuation(financial_line_items, market_cap, verbose_data)
        
        # Combine partial scores or signals
        total_score = quality_analysis["score"] + balance_sheet_analysis["score"] + valuation_analysis["score"]
        max_possible_score = 15  # Adjust weighting as desired
        
        # Generate a simple buy/hold/sell (bullish/neutral/bearish) signal
        if total_score >= 0.7 * max_possible_score:
            signal = "bullish"
        elif total_score <= 0.3 * max_possible_score:
            signal = "bearish"
        else:
            signal = "neutral"
        
        analysis_data[ticker] = {
            "signal": signal,
            "score": total_score,
            "max_score": max_possible_score,
            "quality_analysis": quality_analysis,
            "balance_sheet_analysis": balance_sheet_analysis,
            "valuation_analysis": valuation_analysis
        }

        # Debug:
        logger.debug(f"===Analysis results: signal={signal}, score={total_score}/{max_possible_score}===", module="bill_ackman_agent", ticker=ticker)
        logger.debug(f"- Quality score: {quality_analysis['score']}", module="bill_ackman_agent", ticker=ticker)
        logger.debug(f"- Quality details: {quality_analysis['details']}", module="bill_ackman_agent", ticker=ticker)
        logger.debug(f"- Financial discipline score: {balance_sheet_analysis['score']}", module="bill_ackman_agent", ticker=ticker)
        logger.debug(f"- Financial discipline details: {balance_sheet_analysis['details']}", module="bill_ackman_agent", ticker=ticker) 
        logger.debug(f"- Valuation score: {valuation_analysis['score']}", module="bill_ackman_agent", ticker=ticker)
        logger.debug(f"- Valuation details: {valuation_analysis['details']}", module="bill_ackman_agent", ticker=ticker)
        
        progress.update_status("bill_ackman_agent", ticker, "Generating Ackman analysis")
        ackman_output = generate_ackman_output(
            ticker=ticker, 
            analysis_data=analysis_data,
            model_name=state["metadata"]["model_name"],
            model_provider=state["metadata"]["model_provider"],
        )
        
        ackman_analysis[ticker] = {
            "signal": ackman_output.signal,
            "confidence": ackman_output.confidence,
            "reasoning": ackman_output.reasoning
        }
        
        progress.update_status("bill_ackman_agent", ticker, "Done")
    
    # Wrap results in a single message for the chain
    message = HumanMessage(
        content=json.dumps(ackman_analysis),
        name="bill_ackman_agent"
    )
    
    # Show reasoning if requested
    if state["metadata"]["show_reasoning"]:
        show_agent_reasoning(ackman_analysis, "Bill Ackman Agent")
    
    # Add signals to the overall state
    state["data"]["analyst_signals"]["bill_ackman_agent"] = ackman_analysis

    return {
        "messages": [message],
        "data": state["data"]
    }


def analyze_business_quality(metrics: list, financial_line_items: list, verbose_data: bool = False) -> dict:
    """
    Analyze whether the company has a high-quality business with stable or growing cash flows,
    durable competitive advantages, and potential for long-term growth.
    """
    score = 0
    details = []
    ticker = financial_line_items[0].ticker if financial_line_items else "unknown"

    
    if not metrics or not financial_line_items:
        logger.error(f"Insufficient data to analyze business quality for {ticker}", 
                   module="bill_ackman_agent", ticker=ticker)
        return {
            "score": 0,
            "details": "Insufficient data to analyze business quality"
        }
    
    # overwritten to False for the sake of clarity
    # if verbose_data:
        # logger.debug(f"metrics: {metrics}, financial line items: {financial_line_items}", module="analyze_business_quality", ticker=ticker)
    
    # 1. Multi-period revenue growth analysis
    revenue_data = [(item.report_period, item.revenue) for item in financial_line_items if item.revenue is not None]
    revenues = [r[1] for r in revenue_data]

    growth_rate = None

    if len(revenues) >= 2:
        # Check if overall revenue grew from first to last
        final, initial = revenues[0], revenues[-1]
        initial_date = revenue_data[-1][0] if revenue_data else "unknown"
        final_date = revenue_data[0][0] if revenue_data else "unknown"

        if verbose_data:
            logger.debug(f"Initial revenue ({initial_date}): {initial}", module="analyze_business_quality", ticker=ticker)
            logger.debug(f"Final revenue ({final_date}): {final}", module="analyze_business_quality", ticker=ticker)

        if initial and final and final > initial:
            # Simple growth rate
            growth_rate = (final - initial) / abs(initial)
            if growth_rate > 0.5:  # e.g., 50% growth over the available time
                score += 2
                details.append(f"Revenue grew by {(growth_rate*100):.1f}% over the full period.")
            else:
                score += 1
                details.append(f"Revenue growth is positive but under 50% cumulatively ({(growth_rate*100):.1f}%).")
        else:
            details.append("Revenue did not grow significantly or data insufficient.")
    else:
        logger.warning(f"Not enough revenue data points ({len(revenues)}) to establish multi-period trend", 
                  module="bill_ackman_agent", ticker=ticker)
        details.append("Not enough revenue data for multi-period trend.")

    # Debug
    if verbose_data:
        logger.debug(f"revenues: {revenues}", module="analyze_business_quality", ticker=ticker)
        logger.debug(f"growth rate: {growth_rate}", module="analyze_business_quality", ticker=ticker)
    
    # 2. Operating margin and free cash flow consistency
    # We'll check if operating_margin or free_cash_flow are consistently positive/improving
    fcf_vals = [item.free_cash_flow for item in financial_line_items if hasattr(item, 'free_cash_flow') and item.free_cash_flow is not None]
    op_margin_vals = [item.operating_margin for item in financial_line_items if hasattr(item, 'operating_margin') and item.operating_margin is not None]
    
    if op_margin_vals:
        # Check if the majority of operating margins are > 15%
        above_15 = sum(1 for m in op_margin_vals if m > 0.15)
        if above_15 >= (len(op_margin_vals) // 2 + 1):
            score += 2
            details.append("Operating margins have often exceeded 15%.")
        else:
            details.append("Operating margin not consistently above 15%.")
    else:
        logger.warning(f"No operating margin data available", 
                  module="bill_ackman_agent", ticker=ticker)
        details.append("No operating margin data across periods.")
    
    if fcf_vals:
        # Check if free cash flow is positive in most periods
        positive_fcf_count = sum(1 for f in fcf_vals if f > 0)
        if positive_fcf_count >= (len(fcf_vals) // 2 + 1):
            score += 1
            details.append("Majority of periods show positive free cash flow.")
        else:
            details.append("Free cash flow not consistently positive.")
    else:
        logger.warning(f"No free cash flow data available", 
                  module="bill_ackman_agent", ticker=ticker)
        details.append("No free cash flow data across periods.")

    # Debug
    if verbose_data:
        logger.debug(f"fcf_vals: {fcf_vals}", module="analyze_business_quality", ticker=ticker)
        logger.debug(f"op_margin_vals: {op_margin_vals}", module="analyze_business_quality", ticker=ticker)
        logger.debug(f"score from fcf and op margin: {score}", module="analyze_business_quality", ticker=ticker)
    
    # 3. Return on Equity (ROE) check from the latest metrics
    # (If you want multi-period ROE, you'd need that in financial_line_items as well.)
    latest_metrics = metrics[0]
    if latest_metrics.return_on_equity is None:
        logger.warning(f"ROE data not available in metrics", 
                   module="bill_ackman_agent", ticker=ticker)
    elif latest_metrics.return_on_equity > 0.15:
        score += 2
        details.append(f"High ROE of {latest_metrics.return_on_equity:.1%}, indicating potential moat.")
    elif latest_metrics.return_on_equity:
        details.append(f"ROE of {latest_metrics.return_on_equity:.1%} is not indicative of a strong moat.")
    else:
        details.append("ROE data not available in metrics.")

    # Debug: 
    if verbose_data:
        logger.debug(f"latest metrics RoE: {latest_metrics.return_on_equity}", module="analyze_business_quality", ticker=ticker)
    
    return {
        "score": score,
        "details": "; ".join(details)
    }


def analyze_financial_discipline(metrics: list, financial_line_items: list, verbose_data: bool = False) -> dict:
    """
    Evaluate the company's balance sheet over multiple periods:
    - Debt ratio trends
    - Capital returns to shareholders over time (dividends, buybacks)
    """
    score = 0
    details = []
    ticker = financial_line_items[0].ticker if financial_line_items else "unknown"
    
    if not metrics or not financial_line_items:
        logger.error(f"Insufficient data to analyze financial discipline", 
                   module="analyze_financial_discipline", ticker=ticker)
        return {
            "score": 0,
            "details": "Insufficient data to analyze financial discipline"
        }
    
    # 1. Multi-period debt ratio or debt_to_equity
    # Check if the company’s leverage is stable or improving
    debt_to_equity_vals = [item.debt_to_equity for item in financial_line_items if hasattr(item, 'debt_to_equity') and item.debt_to_equity is not None]
    
    # If we have multi-year data, see if D/E ratio has gone down or stayed <1 across most periods
    if debt_to_equity_vals:
        below_one_count = sum(1 for d in debt_to_equity_vals if d < 1.0)
        if below_one_count >= (len(debt_to_equity_vals) // 2 + 1):
            score += 2
            details.append("Debt-to-equity < 1.0 for the majority of periods.")
        else:
            details.append("Debt-to-equity >= 1.0 in many periods.")
    else:
        # Fallback to total_liabilities/total_assets if D/E not available
        logger.debug(f"No debt_to_equity data, falling back to liabilities/assets ratio", 
                      module="analyze_financial_discipline", ticker=ticker)
        liab_to_assets = []
        for item in financial_line_items:
            if item.total_liabilities and item.total_assets and item.total_assets > 0:
                liab_to_assets.append(item.total_liabilities / item.total_assets)
            else:
                logger.warning(f"Missing total liabilities: {item.total_liabilities} or total assets: {item.total_assets}",
                               module="analyze_financial_discipline", ticker=ticker)
        
        if liab_to_assets:
            below_50pct_count = sum(1 for ratio in liab_to_assets if ratio < 0.5)
            if below_50pct_count >= (len(liab_to_assets) // 2 + 1):
                score += 2
                details.append("Liabilities-to-assets < 50% for majority of periods.")
            else:
                details.append("Liabilities-to-assets >= 50% in many periods.")
        else:
            details.append("No consistent leverage ratio data available.")
    
    # 2. Capital allocation approach (dividends + share counts)
    # If the company paid dividends or reduced share count over time, it may reflect discipline
    # Only try to access the dividends list if it exists but initialize dividends_list first
    dividends_list = []
    if any(hasattr(item, 'dividends_and_other_cash_distributions') for item in financial_line_items):

        dividends_list = [item.dividends_and_other_cash_distributions for item in financial_line_items if item.dividends_and_other_cash_distributions is not None]
        
        if dividends_list:
            # Check if dividends were paid (i.e., negative outflows to shareholders) in most periods
            paying_dividends_count = sum(1 for d in dividends_list if d < 0)
            if paying_dividends_count >= (len(dividends_list) // 2 + 1):
                score += 1
                details.append("Company has a history of returning capital to shareholders (dividends).")
            else:
                details.append("Dividends not consistently paid or no data.")
        else:
            details.append("No dividend data found across periods.")
    else:
        logger.warning(f"No dividend attribute found in line items", 
                   module="analyze_financial_discipline", ticker=ticker)
        details.append("No dividend data found across periods.")
    
    # Check for decreasing share count:
    # We can compare first vs last if we have at least two data points
    # shares = [item.outstanding_shares for item in financial_line_items if item.outstanding_shares is not None]

    # Check for decreasing share count
    shares_with_dates = [(item.report_period, item.outstanding_shares) for item in financial_line_items if item.outstanding_shares is not None]
    shares = [count for _, count in shares_with_dates]
    
    if verbose_data:
        logger.debug(f"Shares with dates (report_period, count): {shares_with_dates}", module="analyze_financial_discipline", ticker=ticker)

    if len(shares) >= 2:
        if shares[0] < shares[-1]:
            score += 1
            details.append("Outstanding shares have decreased over time (possible buybacks).")
        else:
            details.append("Outstanding shares have not decreased over the available periods.")
    else:
        details.append("No multi-period share count data to assess buybacks.")
        logger.warning(f"Not enough share count data for to analyze buyback trends", 
                   module="analyze_financial_discipline", ticker=ticker)
    
    # Debug:
    if verbose_data:
        logger.debug(f"debt_to_equity_vals: {debt_to_equity_vals}", module="analyze_financial_discipline", ticker=ticker)
        logger.debug(f"dividends_list: {dividends_list}", module="analyze_financial_discipline", ticker=ticker)
        logger.debug(f"shares: {shares}", module="analyze_financial_discipline", ticker=ticker)
        logger.debug(f"score: {score}", module="analyze_financial_discipline", ticker=ticker)

    return {
        "score": score,
        "details": "; ".join(details)
    }


def analyze_valuation(financial_line_items: list, market_cap: float, verbose_data: bool = False) -> dict:
    """
    Ackman invests in companies trading at a discount to intrinsic value.
    We can do a simplified DCF or an FCF-based approach.
    This function currently uses the latest free cash flow only, 
    but you could expand it to use an average or multi-year FCF approach.
    """
    ticker = financial_line_items[0].ticker if financial_line_items else "unknown"

    if not financial_line_items or market_cap is None:
        logger.error(f"No financial line items or market_cap available", 
                   module="analyze_valuation", ticker=ticker)
        return {
            "score": 0,
            "details": "Insufficient data to perform valuation"
        }
    
    # Use the most recent item for FCF
    latest = financial_line_items[0]  
    fcf = latest.free_cash_flow if latest.free_cash_flow else 0
    
    # For demonstration, let's do a naive approach:
    growth_rate = 0.06
    discount_rate = 0.10
    terminal_multiple = 15
    projection_years = 5
    
    if fcf <= 0:
        logger.debug(f"Negative free cash flow detected for {ticker} at {latest.report_period}: {fcf}",
                module="analyze_valuation", ticker=ticker)
        return {
            "score": 0,
            "details": f"No positive FCF for valuation; FCF = {fcf}",
            "intrinsic_value": None
        }
    
    present_value = 0
    for year in range(1, projection_years + 1):
        future_fcf = fcf * (1 + growth_rate) ** year
        pv = future_fcf / ((1 + discount_rate) ** year)
        present_value += pv
    
    # Terminal Value
    terminal_value = (fcf * (1 + growth_rate) ** projection_years * terminal_multiple) \
                     / ((1 + discount_rate) ** projection_years)
    intrinsic_value = present_value + terminal_value
    
    # Compare with market cap => margin of safety
    margin_of_safety = (intrinsic_value - market_cap) / market_cap
    
    score = 0
    if margin_of_safety > 0.3:
        score += 3
    elif margin_of_safety > 0.1:
        score += 1
    
    details = [
        f"Calculated intrinsic value: ~{intrinsic_value:,.2f}",
        f"Market cap: ~{market_cap:,.2f}",
        f"Margin of safety: {margin_of_safety:.2%}"
    ]

    if verbose_data:
        logger.debug(f"score: {score}", module="analyze_valuation", ticker=ticker)
        logger.debug(f"details: {details}", module="analyze_valuation", ticker=ticker)
        logger.debug(f"intrinsic_value: {intrinsic_value}", module="analyze_valuation", ticker=ticker)
        logger.debug(f"margin_of_safety: {margin_of_safety}", module="analyze_valuation", ticker=ticker)
    
    return {
        "score": score,
        "details": "; ".join(details),
        "intrinsic_value": intrinsic_value,
        "margin_of_safety": margin_of_safety
    }


def generate_ackman_output(
    ticker: str,
    analysis_data: dict[str, any],
    model_name: str,
    model_provider: str,
) -> BillAckmanSignal:
    """
    Generates investment decisions in the style of Bill Ackman.
    """
    template = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are a Bill Ackman AI agent, making investment decisions using his principles:

            1. Seek high-quality businesses with durable competitive advantages (moats).
            2. Prioritize consistent free cash flow and growth potential.
            3. Advocate for strong financial discipline (reasonable leverage, efficient capital allocation).
            4. Valuation matters: target intrinsic value and margin of safety.
            5. Invest with high conviction in a concentrated portfolio for the long term.
            6. Potential activist approach if management or operational improvements can unlock value.
            
            Rules:
            - Evaluate brand strength, market position, or other moats.
            - Check free cash flow generation, stable or growing earnings.
            - Analyze balance sheet health (reasonable debt, good ROE).
            - Buy at a discount to intrinsic value; higher discount => stronger conviction.
            - Engage if management is suboptimal or if there's a path for strategic improvements.
            - Provide a rational, data-driven recommendation (bullish, bearish, or neutral)."""
        ),
        (
            "human",
            """Based on the following analysis, create an Ackman-style investment signal.

            Analysis Data for {ticker}:
            {analysis_data}

            Return the trading signal in this JSON format:
            {{
              "signal": "bullish/bearish/neutral",
              "confidence": float (0-100),
              "reasoning": "string"
            }}
            """
        )
    ])

    prompt = template.invoke({
        "analysis_data": json.dumps(analysis_data, indent=2),
        "ticker": ticker
    })

    def create_default_bill_ackman_signal():
        return BillAckmanSignal(
            signal="neutral",
            confidence=0.0,
            reasoning="Error in analysis, defaulting to neutral"
        )

    return call_llm(
        prompt=prompt, 
        model_name=model_name, 
        model_provider=model_provider, 
        pydantic_model=BillAckmanSignal, 
        agent_name="bill_ackman_agent", 
        default_factory=create_default_bill_ackman_signal,
    )
