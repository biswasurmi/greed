// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
contract Max {
    int256 b = 3;                        // slot 0
    int256 c = 10;                       // slot 1
    uint256[] arr;                       // slot 2 = length, elements at keccak256(2)+i
    mapping(uint256 => uint256) m;       // slot 3, entries at keccak256(k . 3)
   constructor(int256 _b, int256 _c) {
        c = _c;
        b = _b;}
    function foo(int y) public {
        int x = c;
        x++; y--;
        if (x > y) {
            b++;}}
    function bar() public {
        if (23 < 15) {
            c++;
        }
    }
    function f(int p) public {
        b = p;
        if( b > 5) c++;
    }
    function g(int p) public {
        int x;
        if(p > 100){ x = 1;} else {x = 2;}
        if(x == 1) x = 5;
	if(x == 5) b++;
    }

    // ---- array / mapping cases ----

    function fixedIdx() public {          // constant index: keccak256(2) + 2
        arr[2] = 7;
    }

    function varIdx(uint256 i) public {   // symbolic index: keccak256(2) + i
        arr[i] = 7;
    }

    function mapWrite(uint256 k) public { // keccak256(k . 3)
        m[k] = 9;
    }

    function guardedIdx(uint256 i) public {
        if (i < 3) {
            b = int256(arr[i]);
        }
    }

    function grow() public {              // writes arr.length (slot 2)
        arr.push(1);
    }
}
