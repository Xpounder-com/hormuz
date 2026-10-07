export function PriceAmount({ amount, period }: { amount: string; period: string }) {
  return <div className="price-amount"><strong>{amount}</strong><span className="price-period">{period}</span></div>;
}
