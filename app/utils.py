def calculate_xirr(cash_flows, max_iter=100, tol=1e-5):
    """
    Calculates XIRR using the bisection method.
    cash_flows is a list of (date, amount) tuples.
    """
    if not cash_flows or len(cash_flows) < 2:
        return 0.0

    cash_flows.sort(key=lambda x: x[0])

    # Check for all positive or all negative flows
    if all(cf[1] >= 0 for cf in cash_flows) or all(
        cf[1] <= 0 for cf in cash_flows
    ):
        return 0.0

    d0 = cash_flows[0][0]  # Oldest date as reference

    def npv(rate):
        total = 0.0
        for d, cf in cash_flows:
            days = (d - d0).days
            # Handle rate being -100%
            if 1.0 + rate == 0.0:
                return float("-inf") if days > 0 else float(cf)
            total += float(cf) / ((1.0 + rate) ** (days / 365.0))
        return total

    # Bisection method
    low_rate = -0.9999  # -99.99%
    high_rate = 10.0  # 1000%
    npv_low = npv(low_rate)

    for _ in range(max_iter):
        mid_rate = (low_rate + high_rate) / 2.0
        npv_mid = npv(mid_rate)

        if abs(npv_mid) < tol:
            return mid_rate * 100.0  # Return as percentage

        if npv_low * npv_mid < 0:
            high_rate = mid_rate
        else:
            low_rate = mid_rate
            npv_low = npv_mid  # Update npv_low to the new bound

    return 0.0
