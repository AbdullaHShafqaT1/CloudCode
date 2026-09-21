
def summarize(values):
    if not isinstance(values, (list, tuple)):
        raise TypeError("Input must be a list or tuple.")
    
    if not values:
        raise ValueError("Input list or tuple cannot be empty.")
    
    if not all(isinstance(x, int) for x in values):
        raise TypeError("All elements in the input must be integers.")
    
    count = len(values)
    total = sum(values)
    mean = total / count
    minimum = min(values)
    maximum = max(values)
    
    return {
        "count": count,
        "total": total,
        "mean": mean,
        "minimum": minimum,
        "maximum": maximum
    }