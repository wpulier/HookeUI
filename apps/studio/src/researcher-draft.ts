// The New researcher Tab's start, as pure state (Will, 2026-10-23: the Tab must never claim a researcher started).
// A start is a request sent to the Assistant; the server lists the researcher when it exists. This module decides
// what the Tab may say at each step and which listed researcher is the one this start made; it touches no DOM.
import type {ResearchSession} from './protocol';

export type DraftPhase='idle'|'sent'|'accepted';
export interface DraftStart {session:string;request:string|null;known:ReadonlySet<string>;phase:DraftPhase}
/** `refused`: the answer's own start_researcher refusal (its plain words), when the Assistant took the request but the
 * start was refused (a missing key, the daily cap…): said at once, never waited on. */
export interface AskSettled {requestId:string;ok:boolean;error:string|null;refused?:string|null}

/** The request is on its way: remember which researchers already existed, so a new one is unmistakable. */
export function beginStart(session:string,known:Iterable<string>,request:string|null):DraftStart{
 return {session,request,known:new Set(known),phase:'sent'};
}

/** What the Assistant's answer means for this start: not ours, refused (with the reason), or accepted. */
export function settleStart(start:DraftStart|null,detail:AskSettled|null|undefined):{kind:'ignored'}|{kind:'failed';reason:string}|{kind:'declined';words:string}|{kind:'accepted';start:DraftStart}{
 if(!start||!detail||detail.requestId!==start.request)return {kind:'ignored'};
 if(!detail.ok)return {kind:'failed',reason:(detail.error??'no reason given').replace(/\.$/,'')};
 if(detail.refused)return {kind:'declined',words:detail.refused.trim().replace(/\.$/,'')};
 return {kind:'accepted',start:{...start,phase:'accepted'}};
}

/** The researcher this start made: new since the request, in this Project. Null until the server lists one. */
export function adoptedResearcher(start:DraftStart|null,rows:readonly ResearchSession[],session:string):ResearchSession|null{
 if(!start||start.session!==session)return null;
 return rows.find(row=>!start.known.has(row.rid)&&(row.sitting||session)===start.session)??null;
}

/** The Tab's state word, truthful at each phase. */
export function draftLine(start:DraftStart|null):string{
 return !start?'Not started':start.phase==='accepted'?'Accepted · waiting for the researcher':'Sending…';
}

/** The words under the box after a failure, with what to do. */
export const notStartedWords={
 refused:(reason:string)=>`The Assistant couldn’t take this request (${reason}). Your words are kept; press Start to try again.`,
 declined:(words:string)=>`${words}. Your words are kept.`,
 unavailable:'The Assistant isn’t available here. Your words are kept; try again in a moment.',
 notReady:'The Assistant isn’t ready yet. Your words are kept; try again in a moment.',
 noResearcher:'No researcher has started yet; the Assistant’s reply in the conversation says why. Your words are kept; edit them or press Start again.',
 empty:'Say what it should work on first.',
};
/** How long an accepted request waits for the server to list the researcher before the Tab says so. */
export const ADOPT_WAIT_MS=90_000;
