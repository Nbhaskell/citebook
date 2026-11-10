import type { QuoteOut } from "../api/client";

/** Price lines exactly as quote_price computed them (UI brief section 3, quote block). */
export function QuoteTable({ quote }: { quote: QuoteOut }) {
  return (
    <section className="quote" aria-label="Price">
      {quote.when && <p className="quote-when">{quote.when}</p>}
      <table>
        <tbody>
          {quote.lines.map(([label, amount], index) => (
            <tr key={index}>
              <th scope="row">{label}</th>
              <td className={amount.startsWith("-") ? "credit" : undefined}>{amount}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total</th>
            <td>{quote.total}</td>
          </tr>
        </tfoot>
      </table>
      <p className="quote-kind">
        {quote.is_estimate ? "Estimate: final price confirmed on site" : "Fixed price"}
      </p>
      <p className="quote-note">{quote.note}</p>
    </section>
  );
}
