\## Movebook



When the user asks where a train is, what its next stop is, where it is heading next, or asks for a map of its current progress, identify the exact dated working through RTT first.



Use `getServiceDetails` for the exact `uniqueIdentity`. Determine the train's current operational state from its calls:



\- A call with an actual arrival but no actual departure means the train is currently at that station.

\- Otherwise, the latest call with an actual departure is the last station the train has departed.

\- The first subsequent advertised, non-cancelled passenger call without an actual arrival is the next call.

\- Skip calls which are cancelled when describing the next actual passenger stop.

\- Never infer that a train has passed a location merely from its booked or forecast time.



If the train is between two calls, describe its location as:



"The train has left X and is currently between X and Y. Its next call is Y at \[live/forecast time]."



Do not claim an exact live position unless a source explicitly provides one.



After identifying the last departed call and next call, call `suggestRailRoute` using those two stations. Use the result to provide:



\- the infrastructure route between the calls;

\- railway mileage for that leg;

\- the interactive Movebook map URL;

\- relevant places or lines on the route when useful.



Always describe the Movebook result as a topology-backed infrastructure route. It is not a timetable, a ticketing result, GPS data, or proof that the train is currently at a particular point on that geometry.



For example:



"The train has left Bath Spa and is currently between Bath Spa and Bristol Temple Meads. Bristol Temple Meads is its next call, currently expected at 11:42. The railway route between those stations is about 11.5 miles. \[Open the route map]."



If the train is currently at a station, say so rather than describing it as between calls. The Movebook route may then be used for the leg from the current station to the next call.



When the user asks what comes after the next stop, use the subsequent RTT calls rather than inferring them from Movebook.



For alternative infrastructure routes, only use TIPLOCs returned in the `suggestRailRoute` candidate list and pass them back in travel order. Never invent a via TIPLOC.



If RTT indicates a diversion, cancellation, skipped call, reversal, association, joining or splitting, explain that before presenting the route. Do not silently assume the normal infrastructure path.



If TIGER coach information is also relevant, call `getTigerServiceDetails` only after RTT has identified the exact service. Keep the sources distinct:

\- RTT: dated service identity, live running, platform, allocation and calls.

\- Movebook: infrastructure topology, mileage, geometry and map.

\- TIGER: passenger-facing coach facilities and formation.



Live railway information can change.

